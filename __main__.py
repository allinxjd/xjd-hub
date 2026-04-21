"""python -m hub 启动 Hub 服务."""
import logging, os
logging.basicConfig(level=logging.INFO)

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from hub.server import run_hub_server
run_hub_server(port=int(os.environ.get("HUB_PORT", "8900")), jwt_secret=os.environ.get("JWT_SECRET", "dev-secret"))
