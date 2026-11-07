FROM node:24-alpine

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
