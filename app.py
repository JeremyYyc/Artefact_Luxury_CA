import asyncio
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.deepseek import DeepSeek, ModelError, ModelRateLimited, Settings
from backend.models import GenerateInput, GenerateResponse, Preset, RoutingInfo
from backend.resources import InvalidInput, Resources
from backend.workflow import LOGGER, ReplyWorkflow, ReviewError

SERVICE_VERSION = "0.3.0"
NO_STORE = {"Cache-Control": "no-store"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        settings = Settings()
    except ValidationError:
        raise RuntimeError("请检查 .env 中的 DeepSeek 配置和请求时限。") from None
    app.state.resources = Resources()
    app.state.workflow = ReplyWorkflow(app.state.resources.prompts)
    app.state.timeout = settings.generation_timeout_seconds
    key = settings.llm_api_key.get_secret_value().strip()
    app.state.model = None
    async with httpx.AsyncClient(
        base_url=str(settings.llm_base_url),
        headers={"Authorization": f"Bearer {key}"} if key else {},
        timeout=settings.generation_timeout_seconds,
    ) as client:
        if key:
            app.state.model = DeepSeek(client, settings.llm_model)
        LOGGER.info("resources ready model=%s configured=%s workflow=%s", settings.llm_model,
                    bool(key), app.state.workflow.version)
        yield


app = FastAPI(title="LIUXI Advisor API", version=SERVICE_VERSION, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    return JSONResponse(status_code=422, headers=NO_STORE, content={"detail": "请核对场景、资料及本轮待回复消息或顾问任务。"})


@app.get("/api/health")
async def health(request: Request):
    return {"status": "ok", "version": SERVICE_VERSION, "model_configured": request.app.state.model is not None}


@app.get("/api/presets", response_model=list[Preset])
async def presets(request: Request):
    return request.app.state.resources.presets


@app.get("/api/library")
async def library(request: Request):
    return request.app.state.resources.library()


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(input_data: GenerateInput, request: Request, response: Response):
    state = request.app.state
    request_id = uuid4().hex
    start = perf_counter()
    response.headers.update(NO_STORE)
    if state.model is None:
        raise HTTPException(503, "请先在服务端配置 DeepSeek API Key。", headers=NO_STORE)
    try:
        async with asyncio.timeout(state.timeout):
            scenario = input_data.scenario
            routing = RoutingInfo(mode="selected")
            if scenario is None:
                if input_data.current_message is None and input_data.advisor_goal is None:
                    raise InvalidInput("请填写本轮待回复消息或顾问任务。")
                decision = await state.workflow.identify(input_data, state.model, request_id)
                if decision.scenario == "needs_clarification":
                    raise InvalidInput(decision.question)
                scenario = decision.scenario
                routing = RoutingInfo(mode="automatic", reason=decision.reason)
            context = state.resources.prepare(input_data, scenario=scenario)
            options = await state.workflow.generate(scenario, context, state.model, request_id)
    except InvalidInput as error:
        raise HTTPException(422, str(error), headers=NO_STORE) from None
    except ModelRateLimited:
        LOGGER.warning("generate request_id=%s status=429", request_id)
        raise HTTPException(429, "模型请求较多，请稍后重试。", headers=NO_STORE) from None
    except TimeoutError:
        LOGGER.warning("generate request_id=%s status=504 ms=%d", request_id, (perf_counter() - start) * 1000)
        raise HTTPException(504, "生成超时，已保留输入，请重试。", headers=NO_STORE) from None
    except (ModelError, ReviewError) as error:
        LOGGER.warning("generate request_id=%s status=502 error=%s", request_id, error)
        raise HTTPException(502, "未能生成三条通过校验的回复，请重试。", headers=NO_STORE) from None
    LOGGER.info("generate request_id=%s scenario=%s workflow=%s status=200 ms=%d", request_id,
                scenario, state.workflow.version, (perf_counter() - start) * 1000)
    return GenerateResponse(request_id=request_id, scenario=scenario, workflow_version=state.workflow.version,
                            options=options, routing=routing, basis=context)
