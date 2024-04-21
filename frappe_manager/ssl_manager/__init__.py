from enum import Enum


class SUPPORTED_SSL_TYPES(str, Enum):
    le = 'letsencrypt'
    none = 'disable'
