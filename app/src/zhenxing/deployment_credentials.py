"""Initial deployment credentials supplied explicitly by the local environment."""
import os
def _required(name):
    value=os.environ.get(name,'')
    if not 12<=len(value)<=80:
        raise RuntimeError(name+' must contain 12 to 80 characters; run configure_local.py first')
    return value
def initial_customer_password(username):return username+_required('ZX_INITIAL_CUSTOMER_SUFFIX')
def initial_admin_password():return _required('ZX_INITIAL_ADMIN_PASSWORD')
