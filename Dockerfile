FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY config.py server.py ugos_client.py test_connection.py probe_all_tools.py ./

# NAS 常驻默认走 SSE 远程模式
ENV UGOS_MCP_TRANSPORT=sse \
    UGOS_MCP_HOST=0.0.0.0 \
    UGOS_MCP_PORT=8000

EXPOSE 8000

CMD ["python", "server.py"]
