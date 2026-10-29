"""Create local secrets once. No existing configuration or data is overwritten."""
from pathlib import Path
import secrets,os,ast
root=Path(__file__).resolve().parent
target=root/'.env'
values={'ZX_HTTP_PORT':'18965','ZX_POSTGRES_PORT':'15442','ZX_KNOWLEDGE_PORT':'15443','ZX_EMBEDDING_PORT':'18193',
 'ZX_DATABASE_PASSWORD':secrets.token_hex(20),'ZX_KNOWLEDGE_PASSWORD':secrets.token_hex(20),
 'ZX_INITIAL_CUSTOMER_SUFFIX':secrets.token_urlsafe(18),'ZX_INITIAL_ADMIN_PASSWORD':secrets.token_urlsafe(24),
 'ZX_SEED_AS_OF':'','ZX_ALLOW_PUBLIC_REGISTRATION':'0','ZX_DEEPSEEK_API_KEY':''}
with target.open('x',encoding='utf-8')as f:f.write(''.join(k+'='+v+'\n'for k,v in values.items()))
try:os.chmod(target,0o600)
except OSError:pass
tree=ast.parse((root/'src/zhenxing/accounts.py').read_text(encoding='utf-8'))
names=next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='SEED_USERNAMES'for t in n.targets))
names=(*names,'family_father','family_mother','family_son','family_daughter')
private=root/'var/private';private.mkdir(parents=True,exist_ok=True)
try:os.chmod(private,0o700)
except OSError:pass
with (private/'首次登录.txt').open('x',encoding='utf-8')as f:
 f.write('仅在本机保存，不要上传或分享本文件。\n\n')
 for name in names:f.write(name+'  '+name+values['ZX_INITIAL_CUSTOMER_SUFFIX']+'\n')
 f.write('admin  '+values['ZX_INITIAL_ADMIN_PASSWORD']+'\n')
try:os.chmod(private/'首次登录.txt',0o600)
except OSError:pass
print('Created .env and var/private/首次登录.txt. Add your model key to .env locally. Existing files are never overwritten.')
