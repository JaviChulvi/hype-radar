# Optional Qwen EC2 trial

**OpenRouter remains the application's default.** These files launch a separate,
disposable vLLM service. Qwen is used only when the backend is explicitly started
with the generated private env overlay. No application source, normal `.env` file,
production configuration or default provider is changed.

## Tested configuration

- Region: **eu-west-1 (Ireland)**; one On-Demand `g5.xlarge`, NVIDIA A10G 24 GB.
- AWS NVIDIA Base GPU AMI, Ubuntu 22.04, resolved through the regional SSM parameter.
  The trial used `ami-062e2528f6df34aaf`, NVIDIA driver 595.91.07.
- Encrypted 100 GB gp3 root disk; deleted when the instance is terminated.
- `vllm/vllm-openai:v0.18.1`, pinned to the digest in `bootstrap.sh`.
- `RedHatAI/Qwen3.5-9B-quantized.w4a16`, pinned revision
  `a398088c4228b0ae0c8c78df88fd1e4bf445f068`.
- Original `Qwen/Qwen3.5-9B` tokenizer, pinned revision
  `c202236235762e1c871ad0ccb60c8ee5ba337b9a`. This avoids the quantized
  repository's Transformers 5 tokenizer metadata incompatibility with this image.
- W4A16 checkpoint: 4-bit quantized weights with original-precision activations;
  some operators remain unquantized. Text only, thinking disabled, eager execution,
  **262,144 tokens of context**, at most two concurrent sequences.
- `qwen3_coder` tool-call parser and `qwen3` reasoning parser.

There is no fine-tuning, autoscaling, Kubernetes or speculative decoding.
The GPU memory check accepted the full context window, with approximately 1.23
requests of that size fitting in the cache. Two full-length requests are not a
concurrency guarantee. The weights occupied approximately 9.48 GiB of GPU memory.

## Launch with your own AWS profile

Requires Python 3, AWS CLI, SSH, an authenticated AWS profile with EC2 access,
and at least four G/VT On-Demand vCPUs in Ireland. Authenticate using your usual
AWS login or SSO flow, then run from the repository root:

```sh
aws sts get-caller-identity --profile YOUR_PROFILE --region eu-west-1
python3 infra/qwen-ec2/ec2.py launch --profile YOUR_PROFILE --region eu-west-1
python3 infra/qwen-ec2/ec2.py status
python3 infra/qwen-ec2/ec2.py logs
```

Launching creates paid resources. `launch` defaults to `AWS_PROFILE` or `default`
and `AWS_REGION` or `eu-west-1`. All subsequent commands use the profile and region
saved in `.state/ec2.json`. Capacity retries stay within the selected region;
there is no silent fallback to another region or instance type.

The launcher uses an existing default public VPC/subnet. It creates only one
instance, its root volume, a dedicated security group and an imported SSH key.
It requires EC2 lifecycle/security-group/key-pair permissions, regional AMI lookup
through SSM, and read access to the G/VT service quota. It creates no IAM role.

The AMI supplies the NVIDIA driver. Bootstrap reuses installed Docker/NVIDIA
container tooling, starts systemd services and downloads the approximately 11 GB
checkpoint. The first image pull, model loading and kernel warmup can take 20
minutes or more. Bootstrap logs are in `/var/log/cloud-init-output.log`; model
logs are available through the `logs` command.

If a launch fails after creating resources, rerun the same launch command to
resume or use `delete` to clean up the saved test. Do not remove `.state/` before
cleaning up: it contains the IDs needed to remove only this trial's resources.

## Connect and warm the model

Only SSH from the launching client's public IPv4 `/32` is allowed. The model port
binds to EC2 loopback and requires a generated token. Leave this foreground tunnel
running in its own terminal:

```sh
python3 infra/qwen-ec2/ec2.py tunnel
```

It exposes `http://127.0.0.1:8001/v1` and generates `.state/client.env` with mode
0600. State, keys, tokens and client configuration are Git-ignored. Never copy the
overlay into `backend/.env`, commit it, or print its key. For standalone clients,
it also supplies `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `OPENAI_MODEL`.

From `backend/`, run the smoke check before trying the app:

```sh
uv sync --locked
uv run --locked --env-file ../infra/qwen-ec2/.state/client.env \
  python ../infra/qwen-ec2/smoke.py --agent
```

This checks text generation, exact tool arguments, and the unchanged agent reading
BTC/ETH snapshots, order books and trades from real Hyperliquid data. It does not
start a database evaluator or create/change alerts. First generation after a
restart took 68–90 seconds while additional kernels warmed up.

An optional direct-model check uses more than 64K tokens of synthetic conversation
history and a completed tool result, then verifies a response marker:

```sh
uv run --locked --env-file ../infra/qwen-ec2/.state/client.env \
  python ../infra/qwen-ec2/smoke.py --long-context
```

That check allows five minutes for its large request. It is a context/transport
check, not a model accuracy or throughput benchmark.

## Run the local app with Qwen

Use an already migrated local PostgreSQL database. Stop the existing backend so
only one evaluator owns this application instance. From `backend/`:

```sh
uv run --locked --env-file ../infra/qwen-ec2/.state/client.env \
  uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Run the usual frontend (`npm ci`, then `npm run dev -- --host 127.0.0.1` from
`frontend/`) and open `http://127.0.0.1:5173`. Its proxy points to port 8000.
`/health` should report `chat.model` as `qwen3.5-9b`. The provider label remains
`openrouter` because the existing `ChatOpenRouter` adapter is reused.

To return to normal OpenRouter, stop that backend and start it **without** the
overlay, using your existing OpenRouter key in `backend/.env`:

```sh
uv run --locked uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

The overlay is scoped to the child process; it does not change the parent shell or
persist the provider choice in the app. This trial supports text chat; vLLM does
not provide the app's voice transcription endpoint.

## Verified results and remaining limits

Live checks on October 6, 2026 (Madrid): model API health passed, missing-token
requests returned 401, text generation and exact BTC tool arguments passed.
A BTC/ETH agent run called `list_markets`, `get_market_snapshot`, and both
`get_market_microstructure` tools, ending successfully in 40.25 seconds.
The service advertised 262,144 tokens and accepted that window in its GPU memory
check. The synthetic history/tool-result check passed with **75,450 prompt tokens**
in **26.09 seconds**, returning the expected `CONTEXT_OK` marker. These observations
do not establish full-window reasoning quality.

**The exact browser comparison can still exceed the app's hard-coded 75-second
agent deadline.** Increasing context removed the earlier 16K rejection, but did
not remove this separate timeout. The app also caps submitted history at 20
messages/20,000 characters. Both application limits are unchanged in this draft.
The trial produced funding-unit and OI notional arithmetic mistakes in some
responses; answer correctness needs a separate evaluation.

The first bootstrap encountered an existing Docker package conflict. The script
was corrected to reuse the AMI runtime and rerun successfully. That test instance
retained the historical cloud-init error while `qwen-vllm` was active; new
launches use the corrected bootstrap.

## Stop, restart and remove resources

The instance stops automatically **eight hours after every OS boot**. Stopped EBS
storage remains billable; the timer is not a billing cap. Stop early when finished:

```sh
python3 infra/qwen-ec2/ec2.py stop
python3 infra/qwen-ec2/ec2.py start
python3 infra/qwen-ec2/ec2.py status
```

After a restart, reconnect the tunnel because the public IP can change, then run
the smoke check to warm kernels. Model files and the generated token survive on
the root disk. If your client public IP changes, update only this test security
group's SSH rule to your new exact `/32`.

Remove the trial, including its root volume, security group and AWS key pair:

```sh
python3 infra/qwen-ec2/ec2.py delete
```

The command waits for instance termination and removes local credentials/state.
It does not delete the existing VPC/subnets, databases or other instances.
Ireland Linux On-Demand `g5.xlarge` compute was checked at **$1.123/hour**, about
$8.98 for eight hours, plus gp3 storage, public IPv4, transfer and taxes.

## Instructions for coding agents

1. Read this file and the three scripts before operating AWS. Keep OpenRouter
   defaults and existing app `.env` files unchanged; the Qwen overlay is opt-in.
2. Check authentication, explicit region, quota and existing `.state/` first.
   Never commit state/credentials or launch a second copy of an existing trial.
3. Launch only for an authorized paid test, retain the resource record, connect
   the tunnel, and run the smoke checks before switching the local backend.
4. Keep one Uvicorn worker/evaluator. Validate `/health` and an actual chat stream;
   a healthy model API does not prove the app's request deadline is sufficient.
5. Report failures and model errors honestly. Do not change app limits, add cloud
   infrastructure or tune serving beyond the requested scope without authorization.
6. On cleanup, restore normal OpenRouter startup, stop the tunnel, run `delete`,
   and verify the saved AWS resources are gone. Preserve unrelated resources.

## References

- [Quantized checkpoint](https://huggingface.co/RedHatAI/Qwen3.5-9B-quantized.w4a16)
- [Original Qwen model and native context window](https://huggingface.co/Qwen/Qwen3.5-9B)
- [vLLM Qwen serving recipe](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html)
- [AWS GPU AMI](https://docs.aws.amazon.com/dlami/latest/devguide/aws-deep-learning-x86-base-gpu-ami-ubuntu-22-04.html)
- [NVIDIA container runtime](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- [Ireland EC2 price catalog](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/eu-west-1/index.csv)
