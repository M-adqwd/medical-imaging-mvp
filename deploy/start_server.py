"""启动入口：读 PORT 环境变量拉起服务。

不用 shell 拼 --port $PORT：有的运行环境执行启动命令时不做变量展开，
$PORT 会变成空参数直接报错，这里改成在 Python 里读环境变量。
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    # 通常只暴露一个公网端口，必须绑 0.0.0.0 才能被反向代理转发到
    uvicorn.run("main:app", host="0.0.0.0", port=port, workers=1, log_level="info")
