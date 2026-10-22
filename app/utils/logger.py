import logging
import os
from datetime import datetime
import app.utils.app_utils_config as app_utils_config

log_dir = app_utils_config.system_log_dir
os.makedirs(log_dir, exist_ok=True)
run_time = datetime.now().strftime("%Y%m%d")


class LogColors:
    GREY = "\x1b[38;20m"
    GREEN = "\x1b[32;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"


class ColoredFormatter(logging.Formatter):
    # console_formatter = '[%(asctime)s] %(name)s [%(levelname)s] - %(message)s'
    console_formatter = '[%(levelname)s] - %(message)s'

    FORMATS = {
        logging.DEBUG: LogColors.GREY + console_formatter + LogColors.RESET,
        logging.INFO: LogColors.GREEN + console_formatter + LogColors.RESET,
        logging.WARNING: LogColors.YELLOW + console_formatter + LogColors.RESET,
        logging.ERROR: LogColors.RED + console_formatter + LogColors.RESET,
        logging.CRITICAL: LogColors.BOLD_RED + console_formatter + LogColors.RESET
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)

        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


sys_logger = logging.getLogger("LTA")
sys_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(f"{log_dir}/system_{run_time}.log", encoding="utf-8")

file_formatter = logging.Formatter('[%(asctime)s] %(name)s [%(levelname)s] - %(message)s')
file_handler.setFormatter(file_formatter)

file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter())
console_handler.setLevel(logging.DEBUG)

sys_logger.addHandler(file_handler)
sys_logger.addHandler(console_handler)
