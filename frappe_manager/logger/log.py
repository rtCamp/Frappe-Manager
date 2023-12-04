import logging
import logging.handlers
import os
from frappe_manager import CLI_DIR
import shutil
import gzip
from typing import Dict, Optional, Union


def namer(name):
    return name + ".gz"

def rotator(source, dest):
    with open(source, 'rb') as f_in:
        with gzip.open(dest, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(source)

loggers: Dict[str, logging.Logger] = {}
log_directory = CLI_DIR / 'logs'

def get_logger(log_dir=log_directory, log_file_name='fm') -> logging.Logger:
    """ Creates a Log File and returns Logger object """
    # Build Log File Full Path
    logPath = log_dir / f"{log_file_name}.log"

    # if the directory doesn't exits then create it
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create logger object and set the format for logging and other attributes
    if loggers.get(log_file_name):
        logger: Optional[logging.Logger] = loggers.get(log_file_name)
    else:
        logger: Optional[logging.Logger] = logging.getLogger(log_file_name)
        logger.setLevel(logging.DEBUG)

        # configured to roatate after 10 mb
        handler = logging.handlers.RotatingFileHandler(logPath,'a+',maxBytes=10485760, backupCount=3)
        handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s'))
        handler.rotator = rotator
        logger.addHandler(handler)

        # save logger to dict loggers
        loggers[log_file_name] = logger

    return logger
