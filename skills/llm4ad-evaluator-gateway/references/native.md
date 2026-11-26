# Native gateway startup

Install `llm4ad-next` in a Python 3.12+ environment compatible with the platform,
then install evaluator dependencies in that same environment. For an existing
evaluator, run a real local check:

```bash
llm4ad evaluator-gateway check --evaluator "./evaluator.py:Evaluator" --candidate ./candidate
```

Use `--timeout` if the baseline needs longer. Read the result and verify that the
candidate was actually executed. To test the SDK with its bundled candidate, run
`llm4ad evaluator-gateway check --adapter example`.

After the user authorizes connection, start the gateway. Replace
`YOUR_USER_UUID` with **My user ID** from the platform. The server defaults to
`https://llm4ad-next.cn`. Supply `--server` for another deployment.

```bash
llm4ad evaluator-gateway start --no-ui --name my-evaluator --allow-user YOUR_USER_UUID --evaluator "./evaluator.py:Evaluator" --state-dir ./.llm4ad-gateway
```

Use the optional `llm4ad-next[gateway-ui]` extra for the local dashboard.
Keep a separate persistent state directory for each gateway instance. In the
platform task configuration choose **Remote evaluator** and select the online
evaluator registration.
