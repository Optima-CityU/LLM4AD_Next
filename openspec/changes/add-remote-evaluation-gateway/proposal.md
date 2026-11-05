# Remote evaluation gateways

Add an outbound CLI gateway that shares a local evaluator with explicitly allowed platform users. Candidate jobs are durable and dispatched to one compatible selected gateway. Offline gateways never turn a candidate into a failed evaluation. Local execution continues offline and uploads results when reconnected; unknown execution state requires explicit recovery confirmation.
