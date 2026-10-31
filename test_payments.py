"""Offline unit tests. ALL provider credentials and responses below are test doubles."""
import unittest,tempfile,os,json,time,hmac,hashlib,io,threading,urllib.request,http.cookiejar
from pathlib import Path
from unittest.mock import patch
import payments,server

ENV={'STRIPE_ENABLED':'1','PAYMENT_MODE':'test','STRIPE_SECRET_KEY':'sk_test_UNIT_TEST_NOT_REAL','STRIPE_WEBHOOK_SECRET':'whsec_UNIT_TEST_NOT_REAL','PUBLIC_BASE_URL':'https://unit-test.example'}
class PaymentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old_db=server.DB;server.DB=Path(self.tmp.name)/'store.sqlite3'
  self.env=patch.dict(os.environ,{**ENV,'STYLEHUB_ACCESS_FILE':str(Path(self.tmp.name)/'admin.txt')},clear=True);self.env.start();server.init();self.handler=object.__new__(server.Handler)
  with server.connect() as c:
   c.execute("INSERT INTO orders(id,user_id,name,payment,subtotal,shipping,discount,total,payment_provider,payment_state,payment_ref) VALUES('SHTEST',1,'Unit Test','Card / Stripe',2000,200,0,2200,'stripe','pending','cs_test_unit')")
   c.execute("INSERT INTO order_items(order_id,product_id,name,price,qty,img,color) VALUES('SHTEST',1,'Test',1000,2,'tshirt','Black')");c.execute('UPDATE products SET stock=98 WHERE id=1')
  self.session={'id':'cs_test_unit','metadata':{'order_id':'SHTEST'},'client_reference_id':'SHTEST','currency':'pkr','amount_total':220000,'mode':'payment','livemode':False,'payment_status':'paid','status':'complete'}
 def tearDown(self):self.env.stop();server.DB=self.old_db;self.tmp.cleanup()
 def row(self,c):return c.execute("SELECT * FROM orders WHERE id='SHTEST'").fetchone()
 def signature(self,raw,t=None):
  t=t or int(time.time());sig=hmac.new(ENV['STRIPE_WEBHOOK_SECRET'].encode(),str(t).encode()+b'.'+raw,hashlib.sha256).hexdigest();return f't={t},v1={sig}'
 def test_disabled_without_configuration(self):
  self.assertTrue(payments.stripe_ready())
  with patch.dict(os.environ,{'STRIPE_SECRET_KEY':''}):self.assertFalse(payments.stripe_ready())
  self.assertFalse(next(m for m in payments.methods() if m['id']=='jazzcash')['enabled']);self.assertFalse(next(m for m in payments.methods() if m['id']=='easypaisa')['enabled'])
 def test_signature_and_tampering(self):
  raw=b'{"id":"evt_unit"}';self.assertEqual(payments.verify_stripe_event(raw,self.signature(raw))['id'],'evt_unit')
  with self.assertRaises(payments.PaymentError):payments.verify_stripe_event(raw+b' ',self.signature(raw))
  with self.assertRaises(payments.PaymentError):payments.verify_stripe_event(raw,self.signature(raw,int(time.time())-301))
 def test_order_binding(self):
  with server.connect() as c:
   r=self.row(c);self.assertTrue(payments.check_stripe_session(self.session,r))
   for field,bad in [('amount_total',1),('currency','usd'),('id','cs_test_other'),('client_reference_id','OTHER'),('livemode',True),('metadata',{'order_id':'OTHER'})]:self.assertFalse(payments.check_stripe_session({**self.session,field:bad},r))
 def test_verified_paid_and_no_cancellation(self):
  with server.connect() as c:
   self.handler.apply_stripe(c,self.row(c),self.session);self.assertEqual(self.row(c)['payment_state'],'paid');self.handler.cancel_unpaid(c,'SHTEST','expired');self.assertEqual(self.row(c)['status'],'Processing');self.assertEqual(c.execute('SELECT stock FROM products WHERE id=1').fetchone()[0],98)
 def test_expiration_restock_once(self):
  with server.connect() as c:
   session={**self.session,'payment_status':'unpaid','status':'expired'};self.handler.apply_stripe(c,self.row(c),session);self.handler.apply_stripe(c,self.row(c),session);self.assertEqual(self.row(c)['payment_state'],'expired');self.assertEqual(c.execute('SELECT stock FROM products WHERE id=1').fetchone()[0],100)
 def test_late_payment_requires_review(self):
  with server.connect() as c:
   self.handler.cancel_unpaid(c,'SHTEST','expired');self.handler.apply_stripe(c,self.row(c),self.session);self.assertEqual(self.row(c)['payment_state'],'review');self.assertEqual(self.row(c)['status'],'Cancelled')
 def test_webhook_idempotency(self):
  event={'id':'evt_unit_dedup','type':'checkout.session.completed','data':{'object':{'id':'cs_test_unit'}}};raw=json.dumps(event).encode();self.handler.headers={'Content-Length':str(len(raw)),'Stripe-Signature':self.signature(raw)};responses=[];self.handler.reply=lambda obj,status=200:responses.append((status,obj))
  with patch.object(payments,'stripe_request',return_value=self.session) as remote:
   self.handler.rfile=io.BytesIO(raw);self.handler.stripe_webhook();self.handler.rfile=io.BytesIO(raw);self.handler.stripe_webhook();self.assertEqual(remote.call_count,1)
  self.assertTrue(all(r[0]==200 for r in responses))
  with server.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM payment_events').fetchone()[0],1);self.assertEqual(self.row(c)['payment_state'],'paid')
 def test_mismatched_amount_never_paid(self):
  with server.connect() as c:
   with self.assertRaises(payments.PaymentError):self.handler.apply_stripe(c,self.row(c),{**self.session,'amount_total':1})
   self.assertEqual(self.row(c)['payment_state'],'pending')
 def test_online_order_pending_until_verified_http(self):
  httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
  opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
  base='http://127.0.0.1:'+str(httpd.server_port);csrf=''
  def req(path,data=None):
   request=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-CSRF-Token':csrf})
   with opener.open(request) as response:return json.load(response)
  try:
   auth=req('/api/register',{'name':'Test Only','email':'unit@example.com','password':'unit-test-password'});csrf=auth['csrf']
   mock_session={'id':'cs_test_created','url':'https://checkout.stripe.com/c/pay/test_only'}
   with patch.object(payments,'start_stripe',return_value=mock_session):
    result=req('/api/orders',{'name':'Test Only','phone':'03001234567','address':'12 Unit Test Street','city':'Lahore','postal':'54000','payment':'Card / Stripe','items':[{'id':1,'qty':1}],'total':1})
   self.assertEqual(result['order']['payment_state'],'pending');self.assertNotEqual(result['order']['total'],1);self.assertEqual(result['checkout_url'],mock_session['url']);self.assertEqual(req('/api/orders')['orders'][0]['payment_state'],'pending')
   oid=result['order']['id'];amount=result['order']['total']*100
   verified={**self.session,'id':mock_session['id'],'metadata':{'order_id':oid},'client_reference_id':oid,'amount_total':amount}
   with patch.object(payments,'stripe_request',return_value=verified):self.assertEqual(req('/api/payments/refresh',{'order_id':oid})['order']['payment_state'],'paid')
  finally:httpd.shutdown();httpd.server_close();thread.join()
 def test_jazzcash_signature_primitive(self):
  fields={'pp_Amount':'10000','pp_TxnRefNo':'UNIT_TEST','pp_MerchantID':'NOT_A_MERCHANT'};fields['pp_SecureHash']=payments.jazzcash_signature(fields,'unit-test-salt');self.assertTrue(payments.verify_jazzcash_signature(fields,'unit-test-salt'));fields['pp_Amount']='1';self.assertFalse(payments.verify_jazzcash_signature(fields,'unit-test-salt'))
if __name__=='__main__':unittest.main()
