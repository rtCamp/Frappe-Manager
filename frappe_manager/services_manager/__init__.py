from enum import Enum


class ServicesEnum(str, Enum):
    mariadb = "mariadb"
    nginx_proxy = "nginx-proxy"
    all = "all"
