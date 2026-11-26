# Docker gateway startup

Use the installed SDK's scaffold for a new Docker workspace. Set the intended
platform address and replace `YOUR_USER_UUID` with the user ID to authorize.

```bash
llm4ad evaluator-gateway init ./gateway --name my-evaluator --allow-user YOUR_USER_UUID
cd gateway
```

Use `--server` on `init` for a platform other than `https://llm4ad-next.cn`.
Update the generated evaluator and candidate, `requirements.txt`, and any
read-only mounts or device mappings needed by the evaluator. Keep `.env`, wheels,
logs, and gateway state outside source control. If the required SDK is unpublished,
place its locally built wheel in the generated `wheels/` directory.

```bash
docker compose build gateway
docker compose run --rm --no-deps gateway llm4ad evaluator-gateway check --evaluator /evaluator/evaluator.py:Evaluator --candidate /evaluator/candidate --state-dir /state/check
```

After the real evaluation succeeds and the user authorizes connection, run
`docker compose up -d gateway`. Keep the `./state` mount across restarts. The
generated dashboard binds to the host loopback by default. Check readiness and
select the authorized registration in the platform's **Remote evaluator** picker.
