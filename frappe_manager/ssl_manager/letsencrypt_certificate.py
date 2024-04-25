from pydantic import EmailStr
from frappe_manager.ssl_manager.certificate import SSLCertificate

class LetsencryptSSLCertificate(SSLCertificate):
    email: EmailStr
