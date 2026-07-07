"""统一结构化日志。不打印密钥与完整 PII。"""
import logging
import sys

logger = logging.getLogger("fitness_agent")

if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
