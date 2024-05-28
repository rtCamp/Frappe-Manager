from enum import Enum

class ServicesEnum(str, Enum):
    global_db= "global-db"
    global_nginx_proxy="global-nginx-proxy"
    all="all"
