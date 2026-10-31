"""Integration tests against a running local server. Creates QA-prefixed records."""
import urllib.request,urllib.error,http.cookiejar,json,time,re
from pathlib import Path
BASE='http://127.0.0.1:3000'
class Client:
 def __init__(self):self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));self.csrf=''
 def req(self,path,data=None,method=None,expected=200,csrf=True):
  req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,method=method or ('POST' if data is not None else 'GET'),headers={'Content-Type':'application/json',**({'X-CSRF-Token':self.csrf} if csrf else {})})
  try:r=self.opener.open(req);status=r.status;body=r.read()
  except urllib.error.HTTPError as e:status=e.code;body=e.read()
  assert status==expected,(path,status,body[:200])
  try:return json.loads(body)
  except:return body
c=Client();c.req('/api/admin/dashboard',expected=403);c.req('/data/store.sqlite3',expected=404);c.req('/server.py',expected=404)
u=c.req('/api/register',{'name':'QA API Customer','email':f'qa{int(time.time())}@example.com','password':'ApiTestPass!123'});c.csrf=u['csrf']
assert c.req('/api/session')['user']['name']=='QA API Customer'
c.req('/api/admin/dashboard',expected=403)
c.req('/api/address',{'address':'12 Test Street, Lahore 54000'},csrf=False,expected=403)
c.req('/api/address',{'address':'12 Test Street, Lahore 54000'});assert c.req('/api/address')['address'].startswith('12 Test')
c.req('/api/wishlist',{'ids':[1,6]});assert c.req('/api/wishlist')['ids']==[1,6]
ps=c.req('/api/products')['products'];p=next(p for p in ps if p['id']==6)
data={'name':'QA API Customer','phone':'03001234567','address':'12 Test Street, Lahore','city':'Lahore','postal':'54000','payment':'Cash on Delivery','coupon':'STYLE10','items':[{'id':6,'qty':2,'price':1}],'total':1}
c.req('/api/orders',{**data,'payment':'Card Payment'},expected=400)
o=c.req('/api/orders',data,expected=201)['order'];assert o['total']==p['price']*2-round(p['price']*2*.1);assert next(x for x in c.req('/api/products')['products'] if x['id']==6)['stock']==p['stock']-2
assert len(c.req('/api/orders')['orders'])==1
other=Client();r=other.req('/api/register',{'name':'QA Other','email':f'qa{int(time.time())}x@example.com','password':'ApiTestPass!123'});other.csrf=r['csrf'];assert not other.req('/api/orders')['orders']
a=Client();access=(Path(__file__).resolve().parent.parent/'StyleHub-admin-access.txt').read_text();r=a.req('/api/login',{'email':re.search(r'Email: (.+)',access)[1],'password':re.search(r'Password: (.+)',access)[1]});a.csrf=r['csrf'];assert a.req('/api/admin/dashboard')['orders']
a.req('/api/admin/orders/'+o['id'],{'status':'Shipped'},method='PATCH');assert c.req('/api/orders')['orders'][0]['status']=='Shipped'
a.req('/api/admin/orders/'+o['id'],{'status':'Cancelled'},method='PATCH');assert next(x for x in c.req('/api/products')['products'] if x['id']==6)['stock']==p['stock']
a.req('/api/admin/orders/'+o['id'],{'status':'Shipped'},method='PATCH',expected=400)
c.req('/api/logout',{});assert c.req('/api/session')['user'] is None;c.req('/api/orders',expected=401)
print('PASS: session persistence, auth isolation, admin authorization, CSRF, wishlist, address, server-side pricing, coupon, stock atomicity, status tracking, cancellation restock, source/DB protection, logout.')
