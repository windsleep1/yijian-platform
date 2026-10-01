FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh && chmod +x /usr/local/bin/docker-entrypoint.sh

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ⚠️ 探活地址读 `${PORT}`：托管平台（Render / Railway / Fly）会**注入 PORT**
#    并要求应用监听它。写死 8000 会让 Docker 自检一直失败（而应用其实活着）。
#    本地 docker compose 不设 PORT ⇒ 回落到 8000，行为与以前一致。
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/v1/health" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# ★★ 这里**必须**是 shell 形式（`sh -c`），不能是 JSON 数组形式。
#    判据：**JSON 数组形式的 CMD 不做变量展开** —— 写成
#    `CMD ["uvicorn", ..., "--port", "${PORT:-8000}"]` 会把 `${PORT:-8000}`
#    当成**字面量端口**传给 uvicorn，然后报 `Invalid value for '--port'`。
#    这是"看起来对、跑起来才发现"的那一类，所以把理由钉在它旁边。
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
