#!/usr/bin/env python3
"""StyleHub: dependency-free local store server. See README.md before deployment."""
import os,json,sqlite3,secrets,hashlib,hmac,time,re,threading,uuid
import payments
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('STYLEHUB_DB_PATH',str(ROOT/'data'/'store.sqlite3')))
LOCK=threading.Lock()
RATE={}
STATUSES=['Processing','Shipped','Out for Delivery','Delivered','Cancelled']
class StoreConnection(sqlite3.Connection):
 def __exit__(self,*args):
  try:return super().__exit__(*args)
  finally:self.close()
def connect():
 c=sqlite3.connect(DB,factory=StoreConnection);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
def password_hash(p,salt=None):
 salt=salt or secrets.token_hex(16)
 return salt+':'+hashlib.scrypt(p.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def password_ok(p,stored):return hmac.compare_digest(password_hash(p,stored.split(':')[0]),stored)
def init():
 DB.parent.mkdir(parents=True,exist_ok=True)
 with connect() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT DEFAULT 'customer',created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),csrf TEXT,expires REAL);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT,price INTEGER,img TEXT,category TEXT,color TEXT,stock INTEGER DEFAULT 100,active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),name TEXT,phone TEXT,address TEXT,city TEXT,postal TEXT,payment TEXT,subtotal INTEGER,shipping INTEGER,discount INTEGER,total INTEGER,status TEXT DEFAULT 'Processing',created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY,order_id TEXT REFERENCES orders(id),product_id INTEGER,name TEXT,price INTEGER,qty INTEGER,img TEXT,color TEXT);
CREATE TABLE IF NOT EXISTS addresses(user_id INTEGER PRIMARY KEY REFERENCES users(id),address TEXT);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,name TEXT,email TEXT,message TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS wishlist(user_id INTEGER REFERENCES users(id),product_id INTEGER REFERENCES products(id),PRIMARY KEY(user_id,product_id));
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),product_id INTEGER REFERENCES products(id),rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),title TEXT,body TEXT,visible INTEGER DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,product_id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,order_id TEXT REFERENCES orders(id),status TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
''')
  columns={r[1] for r in c.execute('PRAGMA table_info(orders)')}
  for col,definition in [('payment_state',"TEXT DEFAULT 'cod_due'"),('payment_ref','TEXT'),('payment_provider',"TEXT DEFAULT 'cod'")]:
   if col not in columns:c.execute(f'ALTER TABLE orders ADD COLUMN {col} {definition}')
  c.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_payment_ref ON orders(payment_ref) WHERE payment_ref IS NOT NULL')
  c.execute('CREATE TABLE IF NOT EXISTS payment_events(id TEXT PRIMARY KEY,provider TEXT,order_id TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
  if not c.execute('SELECT 1 FROM products LIMIT 1').fetchone():
   js=(ROOT/'app.js').read_text(); entries=re.findall(r"\{id:(\d+),name:(\"[^\"]+\"|'[^']+'),price:(\d+),img:'([^']+)',category:'([^']+)',color:'([^']+)'\}",js)
   for id,name,price,img,cat,color in entries:c.execute('INSERT INTO products(id,name,price,img,category,color) VALUES(?,?,?,?,?,?)',(int(id),name[1:-1],int(price),img,cat,color))
  if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
   pw=os.environ.get('STYLEHUB_ADMIN_PASSWORD') or secrets.token_urlsafe(18)
   email=os.environ.get('STYLEHUB_ADMIN_EMAIL','admin@stylehub.local')
   c.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,'admin')",('Store Admin',email,password_hash(pw)))
   access=Path(os.environ.get('STYLEHUB_ACCESS_FILE',str(ROOT.parent/'StyleHub-admin-access.txt')));access.parent.mkdir(parents=True,exist_ok=True);access.write_text(f'StyleHub local administrator\nEmail: {email}\nPassword: {pw}\n\nSign in through the website login page, then open Admin Dashboard.\nKeep this file private. Do not upload it to your public host.\nChange your password in Profile Settings after first login.\n')
   os.chmod(access,0o600)
  c.execute("INSERT OR IGNORE INTO settings VALUES('store_name','StyleHub')")
 os.chmod(DB,0o600)
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
 def log_message(self,fmt,*args):print('%s %s'%(self.address_string(),fmt%args),flush=True)
 def end_headers(self):
  self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','same-origin');super().end_headers()
 def reply(self,obj,status=200,cookie=None):
  raw=json.dumps(obj).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)))
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(raw)
 def session(self,c):
  try:
   cookie=SimpleCookie(self.headers.get('Cookie',''));token=cookie['stylehub_session'].value
   return c.execute('SELECT s.*,u.name,u.email,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE token=? AND expires>?',(token,time.time())).fetchone()
  except (KeyError,ValueError):return None
 def cookie(self,token,expire=False):
  secure='; Secure' if self.headers.get('X-Forwarded-Proto')=='https' or os.environ.get('COOKIE_SECURE')=='1' else ''
  return f'stylehub_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={0 if expire else 604800}{secure}'
 def do_GET(self):
  p=urlparse(self.path).path
  if p.startswith('/api/'):
   try:self.api('GET',p,{})
   except Exception as e:print(type(e).__name__,str(e),flush=True);self.reply({'error':'Server error. Please try again.'},500)
   return
  # Explicit allowlist: never serve source, credentials or the SQLite database.
  if p in ['/','/index.html','/app.js','/store.js','/style.css','/polish.css','/payment-ui.js','/reviews.js','/reviews.css'] or re.fullmatch(r'/assets/[a-zA-Z0-9_-]+\.png',p):super().do_GET()
  else:self.send_error(404)
 def do_HEAD(self):
  p=urlparse(self.path).path
  if p in ['/','/index.html','/app.js','/store.js','/style.css','/polish.css','/payment-ui.js','/reviews.js','/reviews.css'] or re.fullmatch(r'/assets/[a-zA-Z0-9_-]+\.png',p):super().do_HEAD()
  else:self.send_error(404)
 def do_POST(self):
  if urlparse(self.path).path=='/api/payments/stripe/webhook':return self.stripe_webhook()
  self.mutate('POST')
 def do_PATCH(self):self.mutate('PATCH')
 def do_DELETE(self):self.mutate('DELETE')
 def mutate(self,method):
  origin=self.headers.get('Origin')
  if origin and urlparse(origin).netloc!=self.headers.get('Host'):return self.reply({'error':'Cross-site request blocked'},403)
  try:
   n=int(self.headers.get('Content-Length','0'))
   if n>65536:return self.reply({'error':'Request too large'},413)
   d=json.loads(self.rfile.read(n) or b'{}')
   if not isinstance(d,dict):raise ValueError()
  except (ValueError,json.JSONDecodeError):return self.reply({'error':'Invalid request'},400)
  try:self.api(method,urlparse(self.path).path,d)
  except (ValueError,TypeError) as e:self.reply({'error':str(e) or 'Invalid data'},400)
  except Exception as e:print(type(e).__name__,str(e),flush=True);self.reply({'error':'Server error. Please try again.'},500)
 def api(self,method,p,d):
  def text(key,lo=1,hi=200):
   v=str(d.get(key,'')).strip()
   if not lo<=len(v)<=hi:raise ValueError(f'{key.replace("_"," ").capitalize()} must be {lo}–{hi} characters.')
   return v
  def email():
   v=text('email',5,150).lower()
   if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v):raise ValueError('Enter a valid email address.')
   return v
  with connect() as c:
   s=self.session(c)
   if method!='GET' and s and p not in ['/api/login','/api/register']:
    if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']):return self.reply({'error':'Session expired. Refresh and try again.'},403)
   if p.startswith('/api/admin/') and (not s or s['role']!='admin'):return self.reply({'error':'Administrator access required'},403)
   if method=='GET' and p=='/api/payments/methods':return self.reply({'methods':payments.methods()})
   if method=='GET' and p=='/api/admin/payments':return self.reply(payments.status())
   if p=='/api/payments/refresh' and method=='POST':
    if not s:return self.reply({'error':'Please sign in'},401)
    oid=text('order_id');row=c.execute('SELECT * FROM orders WHERE id=? AND user_id=?',(oid,s['user_id'])).fetchone()
    if not row:return self.reply({'error':'Order not found'},404)
    if row['payment_provider']=='stripe' and row['payment_ref'] and row['payment_state'] not in ['paid','review']:
     session=payments.stripe_request('checkout/sessions/'+row['payment_ref'])
     if d.get('cancel') and session.get('status')=='open':session=payments.stripe_request('checkout/sessions/'+row['payment_ref']+'/expire',{})
     c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone();self.apply_stripe(c,row,session)
    return self.reply({'order':self.order(c,c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone())})
   if method=='GET' and p=='/api/session':
    return self.reply({'user':{k:s[k] for k in ['name','email','role']} if s else None,'csrf':s['csrf'] if s else None})
   if method=='GET' and p=='/api/settings':return self.reply({'settings':dict(c.execute('SELECT key,value FROM settings'))})
   if p=='/api/wishlist':
    if not s:return self.reply({'error':'Please sign in'},401)
    if method=='GET':return self.reply({'ids':[r[0] for r in c.execute('SELECT product_id FROM wishlist WHERE user_id=?',(s['user_id'],))]})
    if method=='POST':
     ids=d.get('ids',[])
     if not isinstance(ids,list) or len(ids)>200:raise ValueError('Invalid wishlist')
     c.execute('DELETE FROM wishlist WHERE user_id=?',(s['user_id'],))
     for pid in set(int(x) for x in ids):
      if c.execute('SELECT 1 FROM products WHERE id=? AND active=1',(pid,)).fetchone():c.execute('INSERT INTO wishlist VALUES(?,?)',(s['user_id'],pid))
     return self.reply({'ok':True})
   if method=='GET' and p=='/api/products':return self.reply({'products':[dict(r) for r in c.execute('SELECT p.*,COALESCE(ROUND(AVG(r.rating),1),0) AS review_average,COUNT(r.id) AS review_count FROM products p LEFT JOIN reviews r ON r.product_id=p.id AND r.visible=1 WHERE p.active=1 GROUP BY p.id')]})
   if method=='POST' and p in ['/api/login','/api/register']:
    key=(self.client_address[0],p);now=time.time()
    with LOCK:
     RATE[key]=[t for t in RATE.get(key,[]) if t>now-600]
     if len(RATE[key])>=25:return self.reply({'error':'Too many attempts. Try again in 10 minutes.'},429)
     RATE[key].append(now)
    em=email();pw=text('password',8,128)
    if p=='/api/register':
     name=text('name',2,80)
     if c.execute('SELECT 1 FROM users WHERE email=?',(em,)).fetchone():return self.reply({'error':'An account with that email already exists.'},409)
     cur=c.execute('INSERT INTO users(name,email,password) VALUES(?,?,?)',(name,em,password_hash(pw)));uid=cur.lastrowid
    else:
     row=c.execute('SELECT * FROM users WHERE email=?',(em,)).fetchone()
     if not row or not password_ok(pw,row['password']):return self.reply({'error':'Email or password is incorrect.'},401)
     uid=row['id']
    token=secrets.token_urlsafe(32);csrf=secrets.token_hex(24)
    c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,csrf,now+604800))
    # Store opaque random token hash in the cookie: the DB identifier is itself unguessable.
    token=hashlib.sha256(token.encode()).hexdigest()
    u=c.execute('SELECT name,email,role FROM users WHERE id=?',(uid,)).fetchone()
    return self.reply({'user':dict(u),'csrf':csrf},cookie=self.cookie(token))
   if p=='/api/logout' and method=='POST':
    if s:c.execute('DELETE FROM sessions WHERE token=?',(s['token'],))
    return self.reply({'ok':True},cookie=self.cookie('',True))
   if p=='/api/profile' and method=='PATCH':
    if not s:return self.reply({'error':'Please sign in'},401)
    name=text('name',2,80);em=email()
    existing=c.execute('SELECT id FROM users WHERE email=? AND id!=?',(em,s['user_id'])).fetchone()
    if existing:return self.reply({'error':'That email is already in use'},409)
    c.execute('UPDATE users SET name=?,email=? WHERE id=?',(name,em,s['user_id']))
    if d.get('new_password'):
     old=text('current_password',8,128);new=text('new_password',8,128)
     stored=c.execute('SELECT password FROM users WHERE id=?',(s['user_id'],)).fetchone()[0]
     if not password_ok(old,stored):raise ValueError('Current password is incorrect.')
     c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(new),s['user_id']))
     c.execute('DELETE FROM sessions WHERE user_id=? AND token!=?',(s['user_id'],s['token']))
    return self.reply({'user':{'name':name,'email':em,'role':s['role']}})
   if p=='/api/address':
    if not s:return self.reply({'error':'Please sign in'},401)
    if method=='GET':
     row=c.execute('SELECT address FROM addresses WHERE user_id=?',(s['user_id'],)).fetchone();return self.reply({'address':row[0] if row else ''})
    if method=='POST':
     a=text('address',10,1000);c.execute('INSERT OR REPLACE INTO addresses VALUES(?,?)',(s['user_id'],a));return self.reply({'ok':True})
   if p.startswith('/api/reviews/'):
    try:pid=int(p.rsplit('/',1)[-1])
    except ValueError:raise ValueError('Invalid product')
    if not c.execute('SELECT 1 FROM products WHERE id=? AND active=1',(pid,)).fetchone():return self.reply({'error':'Product not found'},404)
    eligible=bool(s and c.execute("SELECT 1 FROM orders o JOIN order_items i ON i.order_id=o.id WHERE o.user_id=? AND i.product_id=? AND o.status='Delivered' AND (o.payment_provider='cod' OR o.payment_state='paid') LIMIT 1",(s['user_id'],pid)).fetchone())
    if method=='GET':
     summary=c.execute('SELECT COUNT(*) AS count,COALESCE(ROUND(AVG(rating),1),0) AS average FROM reviews WHERE product_id=? AND visible=1',(pid,)).fetchone()
     rows=c.execute('SELECT r.id,r.rating,r.title,r.body,r.created,r.updated,u.name FROM reviews r JOIN users u ON u.id=r.user_id WHERE r.product_id=? AND r.visible=1 ORDER BY r.created DESC,r.id DESC LIMIT 100',(pid,)).fetchall()
     result=[]
     for row in rows:
      item=dict(row);parts=item.pop('name').split();item['display_name']=parts[0]+(' '+parts[-1][0]+'.' if len(parts)>1 else '') if parts else 'Customer';item['verified_purchase']=True;result.append(item)
     own=c.execute('SELECT id,rating,title,body,visible,updated FROM reviews WHERE product_id=? AND user_id=?',(pid,s['user_id'])).fetchone() if s else None
     distribution={str(i):0 for i in range(1,6)}
     for row in c.execute('SELECT rating,COUNT(*) AS count FROM reviews WHERE product_id=? AND visible=1 GROUP BY rating',(pid,)):distribution[str(row['rating'])]=row['count']
     return self.reply({'summary':dict(summary),'distribution':distribution,'reviews':result,'can_review':eligible,'own_review':dict(own) if own else None})
    if method=='POST':
     if not s:return self.reply({'error':'Please sign in to write a review.'},401)
     if not eligible:return self.reply({'error':'Only customers with a delivered order for this product can review it.'},403)
     rating=d.get('rating')
     if type(rating) is not int or not 1<=rating<=5:raise ValueError('Choose a rating from 1 to 5 stars.')
     title=text('title',2,100);body=text('body',10,2000)
     c.execute('INSERT INTO reviews(user_id,product_id,rating,title,body) VALUES(?,?,?,?,?) ON CONFLICT(user_id,product_id) DO UPDATE SET rating=excluded.rating,title=excluded.title,body=excluded.body,updated=CURRENT_TIMESTAMP',(s['user_id'],pid,rating,title,body))
     return self.reply({'ok':True})
   if p=='/api/admin/reviews' and method=='GET':
    return self.reply({'reviews':[dict(r) for r in c.execute('SELECT r.*,u.name AS customer,p.name AS product_name,p.img FROM reviews r JOIN users u ON u.id=r.user_id JOIN products p ON p.id=r.product_id ORDER BY r.updated DESC,r.id DESC')]})
   if p.startswith('/api/admin/reviews/') and method=='PATCH':
    rid=int(p.rsplit('/',1)[-1]);visible=d.get('visible')
    if type(visible) is not bool:raise ValueError('Invalid moderation status')
    if not c.execute('SELECT 1 FROM reviews WHERE id=?',(rid,)).fetchone():return self.reply({'error':'Review not found'},404)
    c.execute('UPDATE reviews SET visible=?,updated=CURRENT_TIMESTAMP WHERE id=?',(int(visible),rid));return self.reply({'ok':True})
   if p=='/api/contact' and method=='POST':
    name=text('name',2,80);em=email();msg=text('message',10,3000)
    c.execute('INSERT INTO messages(name,email,message) VALUES(?,?,?)',(name,em,msg));return self.reply({'ok':True})
   if p=='/api/coupon' and method=='POST':
    if text('code').upper()!='STYLE10':raise ValueError('Coupon not found. Use STYLE10 for 10% off.')
    return self.reply({'code':'STYLE10','percent':10})
   if p=='/api/orders' and method=='POST':
    if not s:return self.reply({'error':'Please sign in to place your order.'},401)
    name=text('name',2,80);phone=text('phone',8,25);address=text('address',10,500);city=text('city',2,100);postal=text('postal',3,15)
    pay=d.get('payment');provider='cod' if pay=='Cash on Delivery' else 'stripe' if pay=='Card / Stripe' else None
    if not provider or provider=='stripe' and not payments.stripe_ready():raise ValueError('This payment method is not configured. Please choose Cash on Delivery.')
    items=d.get('items',[])
    if not isinstance(items,list) or not 1<=len(items)<=50:raise ValueError('Your cart is empty or too large.')
    quantities={}
    for item in items:
     pid=int(item['id']);qty=int(item['qty'])
     if not 1<=qty<=50:raise ValueError('Quantity must be between 1 and 50.')
     quantities[pid]=quantities.get(pid,0)+qty
    c.execute('BEGIN IMMEDIATE');rows=[];subtotal=0
    for pid,qty in quantities.items():
     product=c.execute('SELECT * FROM products WHERE id=? AND active=1',(pid,)).fetchone()
     if not product or product['stock']<qty:raise ValueError('An item in your cart is no longer available in that quantity.')
     rows.append((product,qty));subtotal+=product['price']*qty
    discount=(subtotal+5)//10 if d.get('coupon','').upper()=='STYLE10' else 0
    shipping=0 if subtotal>=2500 else 200;total=subtotal+shipping-discount;oid='SH'+uuid.uuid4().hex[:10].upper()
    c.execute('INSERT INTO orders(id,user_id,name,phone,address,city,postal,payment,subtotal,shipping,discount,total) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(oid,s['user_id'],name,phone,address,city,postal,pay,subtotal,shipping,discount,total))
    for pr,qty in rows:
     c.execute('INSERT INTO order_items(order_id,product_id,name,price,qty,img,color) VALUES(?,?,?,?,?,?,?)',(oid,pr['id'],pr['name'],pr['price'],qty,pr['img'],pr['color']))
     c.execute('UPDATE products SET stock=stock-? WHERE id=?',(qty,pr['id']))
    c.execute('UPDATE orders SET payment_provider=?,payment_state=? WHERE id=?',(provider,'pending' if provider=='stripe' else 'cod_due',oid))
    c.execute('INSERT INTO events(order_id,status) VALUES(?,?)',(oid,'Awaiting Payment' if provider=='stripe' else 'Processing'))
    if d.get('save_address'):c.execute('INSERT OR REPLACE INTO addresses VALUES(?,?)',(s['user_id'],address+', '+city+' '+postal))
    checkout_url=None
    if provider=='stripe':
     c.commit()
     try:
      session=payments.start_stripe(dict(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()),s['email'])
      c.execute('UPDATE orders SET payment_ref=? WHERE id=?',(session['id'],oid));checkout_url=session['url']
     except payments.PaymentError:
      self.cancel_unpaid(c,oid,'failed');c.commit();raise
    return self.reply({'order':self.order(c,c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()),'checkout_url':checkout_url},201)
   if p=='/api/orders' and method=='GET':
    if not s:return self.reply({'error':'Please sign in'},401)
    return self.reply({'orders':[self.order(c,r) for r in c.execute('SELECT * FROM orders WHERE user_id=? ORDER BY created DESC',(s['user_id'],))]})
   if method=='GET' and p=='/api/admin/dashboard':
    revenue=c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status!='Cancelled' AND payment_state IN ('paid','cod_due')").fetchone()[0]
    return self.reply({'products':[dict(r) for r in c.execute('SELECT * FROM products')],'orders':[self.order(c,r) for r in c.execute('SELECT * FROM orders ORDER BY created DESC')],'customers':[dict(r) for r in c.execute("SELECT id,name,email,created FROM users WHERE role='customer'")],'messages':[dict(r) for r in c.execute('SELECT * FROM messages ORDER BY created DESC')],'revenue':revenue,'settings':dict(c.execute('SELECT key,value FROM settings'))})
   if method=='PATCH' and p.startswith('/api/admin/orders/'):
    oid=p.rsplit('/',1)[-1];status=text('status')
    if status not in STATUSES:raise ValueError('Invalid order status')
    old=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if not old:return self.reply({'error':'Order not found'},404)
    if old['payment_provider']!='cod':
     if old['payment_state']!='paid' and status!='Processing':raise ValueError('Online payment must be verified before fulfillment. Use the payment reconciliation flow to cancel unpaid orders.')
     if old['payment_state']=='paid' and status=='Cancelled':raise ValueError('Paid orders require a provider refund first. Automatic refunds are not enabled.')
    if old['status']=='Cancelled' and status!='Cancelled':raise ValueError('Cancelled orders cannot be reopened. Create a new order.')
    if status=='Cancelled' and old['status']!='Cancelled':
     for item in c.execute('SELECT product_id,qty FROM order_items WHERE order_id=?',(oid,)).fetchall():c.execute('UPDATE products SET stock=stock+? WHERE id=?',(item['qty'],item['product_id']))
    if old['status']!=status:
     c.execute('UPDATE orders SET status=? WHERE id=?',(status,oid));c.execute('INSERT INTO events(order_id,status) VALUES(?,?)',(oid,status))
    return self.reply({'ok':True})
   if p=='/api/admin/products' and method=='POST' or p.startswith('/api/admin/products/') and method in ['PATCH','DELETE']:
    pid=int(p.rsplit('/',1)[-1]) if p!='/api/admin/products' else None
    if method=='DELETE':c.execute('UPDATE products SET active=0 WHERE id=?',(pid,));return self.reply({'ok':True})
    name=text('name',2,100);price=int(d.get('price',0));stock=int(d.get('stock',0));cat=text('category',2,80);im=text('img',2,50);color=text('color',1,80)
    if cat not in ['Clothes','Shoes','Gym & Fitness','Health & Beauty','Electronics','Home Essentials','Groceries']:raise ValueError('Select a valid category.')
    if price<1 or price>10000000 or stock<0 or stock>100000:raise ValueError('Enter a valid price and stock quantity.')
    if not re.fullmatch('[a-z]+',im) or not (ROOT/'assets'/f'{im}.png').exists():raise ValueError('Choose a valid product image.')
    if pid:c.execute('UPDATE products SET name=?,price=?,stock=?,category=?,img=?,color=?,active=? WHERE id=?',(name,price,stock,cat,im,color,int(bool(d.get('active',True))),pid))
    else:pid=c.execute('INSERT INTO products(name,price,stock,category,img,color,active) VALUES(?,?,?,?,?,?,?)',(name,price,stock,cat,im,color,int(bool(d.get('active',True))))).lastrowid
    return self.reply({'ok':True,'id':pid})
   if p=='/api/admin/settings' and method=='POST':
    for key in ['store_name','contact_email','phone','address']:
     if key in d:c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(d[key])[:200]))
    return self.reply({'ok':True})
   return self.reply({'error':'Not found'},404)
 def cancel_unpaid(self,c,oid,state):
  row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  if not row or row['payment_state'] in ['paid','review']:return
  if row['status']!='Cancelled':
   for item in c.execute('SELECT product_id,qty FROM order_items WHERE order_id=?',(oid,)).fetchall():c.execute('UPDATE products SET stock=stock+? WHERE id=?',(item['qty'],item['product_id']))
   c.execute("UPDATE orders SET status='Cancelled',payment_state=? WHERE id=?",(state,oid));c.execute('INSERT INTO events(order_id,status) VALUES(?,?)',(oid,'Cancelled'))
 def apply_stripe(self,c,row,session):
  if not payments.check_stripe_session(session,row):raise payments.PaymentError('Payment details did not match the order. Contact the store.')
  if session.get('payment_status')=='paid':
   state='review' if row['status']=='Cancelled' else 'paid'
   if row['payment_state']!=state:
    c.execute('UPDATE orders SET payment_state=? WHERE id=?',(state,row['id']));c.execute('INSERT INTO events(order_id,status) VALUES(?,?)',(row['id'],'Payment Verified' if state=='paid' else 'Payment Requires Review'))
  elif session.get('status')=='expired':self.cancel_unpaid(c,row['id'],'expired')
 def stripe_webhook(self):
  if not payments.stripe_ready():return self.reply({'error':'Stripe is not configured'},503)
  try:
   n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=262144:return self.reply({'error':'Invalid webhook size'},413)
   raw=self.rfile.read(n);event=payments.verify_stripe_event(raw,self.headers.get('Stripe-Signature',''))
  except (ValueError,TypeError):return self.reply({'error':'Invalid webhook signature'},400)
  supported=['checkout.session.completed','checkout.session.async_payment_succeeded','checkout.session.async_payment_failed','checkout.session.expired']
  if event.get('type') not in supported:return self.reply({'received':True})
  try:
   obj=event['data']['object'];ref=obj.get('id','')
   if not re.fullmatch(r'cs_[a-zA-Z0-9_]+',ref):return self.reply({'error':'Invalid session'},400)
   with connect() as c:
    if c.execute('SELECT 1 FROM payment_events WHERE id=?',(event['id'],)).fetchone():return self.reply({'received':True})
    row=c.execute('SELECT * FROM orders WHERE payment_ref=?',(ref,)).fetchone()
    if not row:return self.reply({'error':'Order session not found; retry later'},409)
    # Verify current provider state server-to-server, not the redirect or browser.
    session=payments.stripe_request('checkout/sessions/'+ref)
    c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone()
    if c.execute('SELECT 1 FROM payment_events WHERE id=?',(event['id'],)).fetchone():return self.reply({'received':True})
    self.apply_stripe(c,row,session)
    if event['type']=='checkout.session.async_payment_failed' and session.get('payment_status')!='paid':self.cancel_unpaid(c,row['id'],'failed')
    c.execute('INSERT INTO payment_events(id,provider,order_id) VALUES(?,?,?)',(event['id'],'stripe',row['id']))
   return self.reply({'received':True})
  except (KeyError,ValueError,TypeError):return self.reply({'error':'Webhook reconciliation failed'},400)
  except Exception:return self.reply({'error':'Temporary webhook processing error'},503)
 def order(self,c,r):
  result=dict(r);result['items']=[dict(x) for x in c.execute('SELECT product_id AS id,name,price,qty,img,color FROM order_items WHERE order_id=?',(r['id'],))];result['events']=[dict(x) for x in c.execute('SELECT status,created FROM events WHERE order_id=? ORDER BY id',(r['id'],))];result['date']=r['created'][:10];return result
if __name__=='__main__':
 init();port=int(os.environ.get('PORT','3000'));print(f'StyleHub running on 0.0.0.0:{port}',flush=True);ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
