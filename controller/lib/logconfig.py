# logconfig is a simple logging utility aiming to provide good defaults 'out of the box'
# import logconfig and use logging
import logging
import logging.handlers
import os
import zlib


# default maximum log file size in bytes. When this size is reached, a rotation
# occurs.
DEFAULT_LOG_MAXBYTES = int(100 * 1e6)

# default log backup count: how many log backup files can exist
DEFAULT_LOG_BACKUP_COUNT = 1

log_formatter = logging.Formatter('[%(asctime)s.%(msecs)03d] -%(levelname).1s- : %(message)s',
                                  datefmt='%d-%m-%Y %H:%M:%S')

def namer(name):
   return name + ".gz"
    

def rotator(source, dest):
    with open(source, "rb") as sf:
        data = sf.read()
        compressed = zlib.compress(data, 9)
        with open(dest, "wb") as df:
            df.write(compressed)
    os.remove(source)


def config_default_logger(level=logging.INFO):

    # set up console logging
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(console_handler)
    root_logger.setLevel(level)


def log_to_file(logfile, **kwargs):
    maxBytes    = kwargs.get('maxBytes', DEFAULT_LOG_MAXBYTES)
    backupCount = kwargs.get('backupCount', DEFAULT_LOG_BACKUP_COUNT)

    root_logger = logging.getLogger()
    try:
        file_handler = logging.handlers.RotatingFileHandler(logfile, maxBytes=maxBytes, backupCount=1)
    except PermissionError as e:
        logging.error('error: logging to file failed: {}'.format(str(e)))
        return
    
    file_handler.setFormatter(log_formatter)
    file_handler.rotator = rotator
    file_handler.namer = namer
    root_logger.addHandler(file_handler)

