"""One worker iteration; the caller supplies quotes and frames via trusted adapters."""
from .service import ACTIVE


class Controller:
    """Advance one custody job. A dryrun controller has no broker at all.

    The only code path that can reach ``submit``/``cancel`` is
    ``CustodyService.dispatch_next``. In dryrun the service refuses to dispatch and
    the controller is constructed with ``broker=None``.
    """

    def __init__(self, service, broker=None):
        if broker is None:
            if service.mode != 'dryrun':
                raise ValueError('broker required unless service mode is dryrun')
        elif broker.account != service.account or broker.mode != service.mode:
            raise ValueError('broker binding mismatch')
        self.service, self.broker = service, broker

    def step(self, job_id, now, quote=None, frame=None):
        # Clock first (flatten / must-trade deadline) so they never wait for a bar.
        self.service.heartbeat(job_id, now, quote)
        job = self.service.get_job(job_id)
        failed = False
        if self.broker is not None:
            for order in job['orders']:
                if order['kind'] == 'LIMIT' and order['status'] in ACTIVE - {'CREATED'}:
                    try:
                        update = self.broker.lookup(order['client_order_id'])
                        if update is not None:
                            self.service.apply_update(update, now)
                        else:
                            failed = True
                    except Exception:
                        failed = True
        self.service.heartbeat(job_id, now, quote)
        if frame is not None:
            self.service.on_frame(job_id, frame, now, quote)
        if self.broker is not None:
            self.service.dispatch_next(self.broker, now)
        if failed:
            self.service.flag_attention(job_id, 'RECONCILE_ORDER_STATUS')
        return self.service.get_job(job_id)
