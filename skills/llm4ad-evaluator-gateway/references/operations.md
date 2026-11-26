# Check a gateway and its evaluations

```bash
llm4ad evaluator-gateway health --state-dir ./.llm4ad-gateway
llm4ad evaluator-gateway status --state-dir ./.llm4ad-gateway
```

Use the actual state directory from startup. In the generated Docker workspace,
the container uses `/state`, mapped to `./state` on the host. View startup
messages with `docker compose logs gateway`.

| Symptom | What to check |
| --- | --- |
| Local check fails | Read the actual compile, execution and scoring diagnostics. Confirm inputs and dependencies in the startup environment. |
| Evaluator does not appear | Confirm readiness, platform URL, user UUID authorization and the selected evaluator ID/version. |
| Tasks wait during a disconnect | Keep the same state directory and registration when restarting. The SDK reconnects over SSE. |
| Need raw evidence | Check the evaluation node in the platform for declared attachments and metadata. Open local execution logs in the gateway dashboard. |

For a web ZIP that uses this gateway, follow
`llm4ad --skill cloud-package --topic remote-evaluation`. A local verification
report covers the offline evaluator check. The online registration is selected
after importing the ZIP.
