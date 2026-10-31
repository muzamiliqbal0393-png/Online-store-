"""Server-only payment adapters. Never import secrets into the browser bundle."""
import os,json,time,hmac,hashlib,urllib.request,urllib.error
from urllib.parse import urlencode,urlparse

class PaymentError(ValueError):pass

def base_url():
 value=os.environ.get('PUBLIC_BASE_URL','').rstrip('/')
 parsed=urlparse(value)
 return value if parsed.scheme=='https' and parsed.netloc and parsed.path in ['', '/'] and not parsed.query and not parsed.fragment else ''

def stripe_ready():
 key=os.environ.get('STRIPE_SECRET_KEY','');hook=os.environ.get('STRIPE_WEBHOOK_SECRET','')
 mode=os.environ.get('PAYMENT_MODE','test')
 return bool(base_url() and hook.startswith('whsec_') and key.startswith('sk_'+mode+'_') and mode in ['test','live'] and os.environ.get('STRIPE_ENABLED')=='1')

def methods():
 return [{'id':'cod','name':'Cash on Delivery','enabled':True,'state':'active','description':'Pay in cash when your order arrives.'},
 {'id':'stripe','name':'Card / Stripe','enabled':stripe_ready(),'state':('test' if os.environ.get('PAYMENT_MODE','test')=='test' else 'live') if stripe_ready() else 'not_configured','description':'Secure hosted card checkout.' if stripe_ready() else 'Not configured — requires merchant credentials, HTTPS and a verified webhook.'},
 {'id':'jazzcash','name':'JazzCash','enabled':False,'state':'requires_merchant_setup','description':'Merchant credentials and the approved integration contract are required.'},
 {'id':'easypaisa','name':'Easypaisa','enabled':False,'state':'requires_merchant_setup','description':'Merchant onboarding and the current merchant-specific API contract are required.'}]

def status():
 return {'mode':os.environ.get('PAYMENT_MODE','test'),'currency':'PKR','methods':methods(),'checks':{
 'https_url':bool(base_url()),'stripe_key_present':bool(os.environ.get('STRIPE_SECRET_KEY')),
 'stripe_webhook_present':bool(os.environ.get('STRIPE_WEBHOOK_SECRET')),
 'jazzcash_credentials_present':all(os.environ.get(n) for n in ['JAZZCASH_MERCHANT_ID','JAZZCASH_PASSWORD','JAZZCASH_INTEGRITY_SALT']),
 'easypaisa_credentials_present':all(os.environ.get(n) for n in ['EASYPAISA_STORE_ID','EASYPAISA_USERNAME','EASYPAISA_PASSWORD','EASYPAISA_HASH_KEY'])}}

def stripe_request(path,data=None,idempotency=None):
 if not stripe_ready():raise PaymentError('Stripe is not configured. Choose Cash on Delivery.')
 headers={'Authorization':'Bearer '+os.environ['STRIPE_SECRET_KEY'],'Content-Type':'application/x-www-form-urlencoded','Stripe-Version':'2024-06-20'}
 if idempotency:headers['Idempotency-Key']=idempotency
 req=urllib.request.Request('https://api.stripe.com/v1/'+path,data=urlencode(data).encode() if data is not None else None,headers=headers)
 try:
  with urllib.request.urlopen(req,timeout=20) as response:return json.load(response)
 except (urllib.error.URLError,ValueError,TimeoutError,OSError) as e:raise PaymentError('Payment provider could not be reached. Your order has not been marked paid.') from e

def start_stripe(order,email):
 origin=base_url()
 payload={'mode':'payment','payment_method_types[0]':'card','client_reference_id':order['id'],
 'metadata[order_id]':order['id'],'payment_intent_data[metadata][order_id]':order['id'],
 'customer_email':email,'success_url':origin+'/?payment_return=1#payment/'+order['id'],
 'cancel_url':origin+'/?payment_cancelled=1#payment/'+order['id'],
 'expires_at':str(int(time.time())+1800),'line_items[0][quantity]':'1',
 'line_items[0][price_data][currency]':'pkr','line_items[0][price_data][unit_amount]':str(order['total']*100),
 'line_items[0][price_data][product_data][name]':'StyleHub Order '+order['id']}
 session=stripe_request('checkout/sessions',payload,'stylehub-'+order['id'])
 if not session.get('id','').startswith('cs_') or urlparse(session.get('url','')).hostname!='checkout.stripe.com':raise PaymentError('Unexpected checkout response. Contact the store before retrying.')
 return session

def verify_stripe_event(raw,signature,secret=None,now=None):
 secret=secret or os.environ.get('STRIPE_WEBHOOK_SECRET','')
 if not secret:raise PaymentError('Webhook is not configured')
 try:
  fields={}
  for part in signature.split(','):
   k,v=part.strip().split('=',1);fields.setdefault(k,[]).append(v)
  timestamp=int(fields['t'][0]);expected=hmac.new(secret.encode(),str(timestamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
  if abs((now or time.time())-timestamp)>300 or not any(hmac.compare_digest(expected,x) for x in fields.get('v1',[])):raise ValueError()
  event=json.loads(raw)
  if not isinstance(event,dict) or not str(event.get('id','')).startswith('evt_'):raise ValueError()
  return event
 except (ValueError,KeyError,TypeError):raise PaymentError('Invalid webhook signature')

def check_stripe_session(session,order):
 return bool(session.get('id')==order['payment_ref'] and session.get('metadata',{}).get('order_id')==order['id'] and session.get('client_reference_id')==order['id'] and session.get('currency')=='pkr' and session.get('amount_total')==order['total']*100 and session.get('mode')=='payment' and session.get('livemode')==(os.environ.get('PAYMENT_MODE','test')=='live'))

# JazzCash signing primitives only. NOT an activated payment adapter.
# Current merchant-approved fields/endpoints and response rules must be verified
# before enabling a real wallet flow. A caller cannot enable this with an env flag.
def jazzcash_signature(fields,salt):
 values=[str(fields[k]) for k in sorted(fields) if (k.startswith('pp_') or k.startswith('ppmpf_')) and k!='pp_SecureHash' and fields[k] not in [None,'']]
 return hmac.new(salt.encode(),(salt+'&'+'&'.join(values)).encode(),hashlib.sha256).hexdigest().upper()
def verify_jazzcash_signature(fields,salt):
 supplied=str(fields.get('pp_SecureHash',''))
 return bool(supplied and hmac.compare_digest(jazzcash_signature(fields,salt),supplied.upper()))
