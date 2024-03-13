import logging
import logging.handlers
import os
from frappe_manager import CLI_LOG_DIRECTORY
import shutil
import gzip
from typing import Dict, Optional, Union
from frappe_manager.display_manager.DisplayManager import richprint

# Define MESSAGE log level
CLEANUP = 25

def namer(name):
    return name + ".gz"

def rotator(source, dest):
    with open(source, 'rb') as f_in:
        with gzip.open(dest, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(source)

loggers: Dict[str, logging.Logger] = {}

# "Register" new loggin level
logging.addLevelName(CLEANUP, 'CLEANUP')

class FMLOGGER(logging.Logger):
    def cleanup(self, msg, *args, **kwargs):
        if self.isEnabledFor(CLEANUP):
            self._log(CLEANUP, msg, args, **kwargs)

def get_logger(log_dir=CLI_LOG_DIRECTORY, log_file_name='fm') -> logging.Logger:
    """ Creates a Log File and returns Logger object """
    # Build Log File Full Path
    logPath = log_dir / f"{log_file_name}.log"

    try:
        log_dir.mkdir(parents=False, exist_ok=True)
    except PermissionError as e:
        richprint.exit(f"Logging not working. {e}",os_exit=True)

    # Create logger object and set the format for logging and other attributes
    if loggers.get(log_file_name):
        logger: Optional[logging.Logger] = loggers.get(log_file_name)
    else:
        logging.setLoggerClass(FMLOGGER)
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
