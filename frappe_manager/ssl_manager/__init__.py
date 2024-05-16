from enum import Enum


class SUPPORTED_SSL_TYPES(str, Enum):
    le = 'letsencrypt'
    none = 'disable'


class LETSENCRYPT_PREFERRED_CHALLENGE(str, Enum):
    dns01 = 'dns01'
    http01 = 'http01'
