"""Small authenticated WSGI control API. Event ingestion stays internal."""
from datetime import datetime, timezone
import hmac,json


def create_app(service, token, clock=None):
    if not isinstance(token,str) or len(token)<20: raise ValueError('server bearer token must be at least20 characters')
    clock=clock or (lambda:datetime.now(timezone.utc))
    def view(j):
        return {'job_id':j['id'],'state':j['state'],'mode':j['mode'],**j['request'],'strategy_hash':j['strategy']['sha256'],'strategy_status':j['strategy']['status'],'position_qty':j['position_qty'],'attention':j['attention'],'entry_reason':j['entry_reason'],'exit_reason':j['exit_reason'],'must_enter_at':j['must_enter_at'],'flatten_at':j['flatten_at']}
    def app(environ,start_response):
        def respond(status,data):
            body=json.dumps(data,allow_nan=False).encode();start_response(status,[('Content-Type','application/json'),('Content-Length',str(len(body))),('Cache-Control','no-store')]);return [body]
        supplied=environ.get('HTTP_AUTHORIZATION','')
        if not hmac.compare_digest(supplied.encode(),('Bearer '+token).encode()):return respond('401 Unauthorized',{'error':'unauthorized'})
        method,path=environ.get('REQUEST_METHOD'),environ.get('PATH_INFO','')
        try:
            if method=='GET' and path=='/v1/strategies':return respond('200 OK',{'strategies':service.registry.list(),'mode':service.mode})
            if method=='GET' and path.startswith('/v1/jobs/'):return respond('200 OK',view(service.get_job(path.removeprefix('/v1/jobs/'))))
            if method=='POST' and path.startswith('/v1/jobs/') and path.endswith('/stop'):
                return respond('200 OK',view(service.stop_job(path[len('/v1/jobs/'):-len('/stop')],clock())))
            if method=='POST' and path=='/v1/jobs':
                if environ.get('CONTENT_TYPE','').split(';')[0]!='application/json':return respond('415 Unsupported Media Type',{'error':'application/json required'})
                size=int(environ.get('CONTENT_LENGTH') or 0)
                if not 0<size<=16384:return respond('413 Payload Too Large',{'error':'body must be1..16384 bytes'})
                raw=environ['wsgi.input'].read(size)
                if len(raw)!=size:raise ValueError('truncated request body')
                def unique(pairs):
                    d={}
                    for k,v in pairs:
                        if k in d:raise ValueError('duplicate JSON field: '+k)
                        d[k]=v
                    return d
                request=json.loads(raw,object_pairs_hook=unique)
                return respond('200 OK',view(service.create_job(request,clock())))
            return respond('404 Not Found',{'error':'not_found'})
        except KeyError:return respond('404 Not Found',{'error':'not_found'})
        except (ValueError,TypeError) as exc:return respond('422 Unprocessable Entity',{'error':str(exc)})
    return app
