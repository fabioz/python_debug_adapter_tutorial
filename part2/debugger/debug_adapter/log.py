import threading

_debug_lock = threading.Lock()

DEBUG = True
import os
DEBUG_FILE = os.path.join(os.path.dirname(__file__), '__debug_output__.txt')
 

def debug(msg):
    if DEBUG:
        with _debug_lock:
            open(DEBUG_FILE, 'a+').write(msg)


def debug_exception(msg=None):
    if DEBUG:
        with _debug_lock:
            if msg:
                open(DEBUG_FILE, 'a+').write(msg)
                
            import traceback
            traceback.print_exc(file=open(DEBUG_FILE, 'a+'))
