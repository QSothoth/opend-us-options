"""One worker iteration; caller supplies quotes/frames via trusted adapters."""
from .service import ACTIVE


class Controller:
    def __init__(self,service,broker):
        if broker.account!=service.account or broker.mode!=service.mode:raise ValueError('broker binding mismatch')
        self.service,self.broker=service,broker

    def step(self,job_id,now,quote=None,frame=None,underlying_mark=None,mark_as_of=None):
        # Record watchdog requests even if the broker lookup is unavailable.
        # No order is dispatched until reconciliation has been attempted.
        self.service.heartbeat(job_id,now,quote,underlying_mark,mark_as_of)
        job=self.service.get_job(job_id);failed=False
        for order in job['orders']:
            if order['kind']=='LIMIT' and order['status'] in ACTIVE-{'CREATED'}:
                try:
                    update=self.broker.lookup(order['client_order_id'])
                    if update is not None:self.service.apply_update(update,now)
                    else:failed=True
                except Exception:failed=True
        # Must be scheduled independently of bar arrival, including data outages.
        self.service.heartbeat(job_id,now,quote,underlying_mark,mark_as_of)
        if frame is not None:self.service.on_frame(job_id,frame,now,quote)
        self.service.dispatch_next(self.broker,now)
        if failed:self.service.flag_attention(job_id,'RECONCILE_ORDER_STATUS')
        return self.service.get_job(job_id)
