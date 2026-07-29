# Running the pipeline on AWS

Step by step, from an account you've barely used to a deployed stack, with the
reasoning for each choice. Nothing here needs to be taken on faith — every step
says what it does and why.

**What we're building**

```
EventBridge Scheduler  (05:30 + 16:30, America/Toronto)
        │
        ▼
  Lambda: build          fetch RBOB/FX/survey -> model -> data.json
        │                reads + writes the CSVs through the GitHub API
        ▼
  GitHub repo ──> Pages ──> ESP32 fetches data.json   (URL unchanged)

  Lambda Function URL: log    <- phone Shortcut / ESP32 POST a price
```

**What it costs: $0/month.** Genuinely, not "cheap" — the usage sits inside
permanently-free allowances. Part 2 sets up an alarm anyway, because "should be
free" and "is free" are different claims and only one of them is checkable.

**What is *not* here:** a GasBuddy scraper. Cloudflare's managed challenge is
solved by executing JavaScript; Lambda has no browser, and datacenter IPs are
scored *more* harshly than home ones. Moving hosts does not change that. See
`CLAUDE.md`.

---

## Part 1 — Secure the account before anything runs in it

You have an account you've barely used. Before deploying anything, close the
two holes that cause the horror stories.

### 1.1 Lock the root user

The email address you signed up with is the **root user**. It can do anything,
including closing the account and changing billing. It cannot be restricted.

1. Sign in as root → click your account name (top right) → **Security credentials**.
2. **Assign MFA device.** Use your phone's authenticator app. Not optional — a
   root account without MFA is one password leak away from someone running up a
   five-figure crypto-mining bill in a region you've never heard of.
3. If any **root access keys** exist, delete them. There is no legitimate use
   for them. AWS's own guidance is that they should not exist.

Then stop using root. You'll sign in as root perhaps twice a year, for billing
changes. Everything else uses the identity you make next.

### 1.2 Make a working identity

Two routes. Both end with an admin identity that isn't root.

**IAM user (what this account uses).** IAM → Users → Create user → name it →
attach **AdministratorAccess** directly → create. Then add an MFA device to that
user too, and generate access keys under **Security credentials → Access keys →
Command Line Interface (CLI)**.

```bash
aws configure --profile gasprices     # key id, secret, ca-central-1, json
export AWS_PROFILE=gasprices
aws sts get-caller-identity           # Arn must end in :user/<name>, not :root
```

**IAM Identity Center** is the alternative, and issues *temporary* credentials
instead. More setup (it enables AWS Organizations), and the console layout moves
around, but nothing long-lived lands on disk.

The trade is just that: an IAM user's access key is a permanent secret in
`~/.aws/credentials`. Leaked keys are the most common way hobby accounts get
compromised — committed to a repo, scanned, mining bills within minutes. If you
take this route, never let that file near a repo, and rotate the key if you have
any doubt. Identity Center's credentials expire on their own, which is why AWS
recommends it.

Either way: **enable MFA on the admin identity as well as root**, and stop using
root for daily work.

---

## Part 2 — A budget alarm, before you deploy anything

Do this *before* the first deploy, not after. AWS bills in arrears: by the time
a surprise shows up on a statement it has already happened.

**Billing and Cost Management** → **Budgets** → **Create budget**:

- Type: **Cost budget**
- Period: Monthly, amount: **$1.00**
- Alert at **80% of budgeted amount** → your email

$1 sounds absurdly low. That's the point: this stack should cost nothing, so any
alert at all means something is wrong and you want to hear about it that week,
not next month.

> Budget alerts are *notifications*, not caps. AWS will not stop your resources.
> The alarm buys you a fast reaction, which at these volumes is enough.

---

## Part 3 — Tools on your Mac

```bash
brew install awscli aws-sam-cli
aws --version && sam --version
```

Then connect the CLI to the identity from Part 1:

```bash
aws configure sso
```

It asks for your Identity Center start URL (in the Identity Center console) and
region. Pick **`ca-central-1`** (Montreal) — closest to you, and the data is
Canadian. Name the profile something you'll recognise:

```bash
export AWS_PROFILE=gasprices
aws sts get-caller-identity
```

That last command is the "am I actually who I think I am" check. It should print
your account id and the assumed role. If it errors, nothing later will work —
fix it here.

Credentials expire; when they do, `aws sso login` again. That expiry *is* the
security feature.

---

## Part 4 — The GitHub token, stored properly

The Lambda reads and writes the CSVs through the GitHub API, so it needs a
token.

### 4.1 Create a fine-grained PAT

GitHub → Settings → Developer settings → **Fine-grained personal access tokens**
→ Generate new:

| Field | Value |
|---|---|
| Repository access | **Only select repositories** → `gasprices` |
| Permissions → **Contents** | **Read and write** |
| Permissions → Workflows | **leave off** |
| Expiration | 90 days (calendar a reminder) |

**Contents** is what lets it commit CSVs. **Workflows is deliberately off**: if
this token ever leaks, the holder can write data files but cannot rewrite your
CI to do something worse.

### 4.2 Put it in SSM Parameter Store

```bash
aws ssm put-parameter \
  --name /gasprices/github_token \
  --type SecureString \
  --value "github_pat_xxxxxxxx" \
  --overwrite

aws ssm put-parameter \
  --name /gasprices/ingest_secret \
  --type SecureString \
  --value "$(openssl rand -hex 32)" \
  --overwrite
```

The second is the shared secret your phone and ESP32 will send to prove a log
request is really yours. `openssl rand -hex 32` generates it so you never invent
one badly.

> **Parameter Store, not Secrets Manager.** They look interchangeable. Secrets
> Manager costs **$0.40 per secret per month** — $9.60/year for two secrets, on
> a stack that otherwise costs nothing. Standard SSM parameters are free.
> Secrets Manager earns its price with automatic rotation, which we don't use.

Read one back to confirm it stored (this prints the secret, so don't do it on a
shared screen):

```bash
aws ssm get-parameter --name /gasprices/github_token --with-decryption \
  --query Parameter.Value --output text
```

---

## Part 5 — Deploy

```bash
sam build
sam deploy --guided
```

`--guided` asks a series of questions and writes your answers to
`samconfig.toml`, so later deploys are just `sam deploy`. Answers:

- Stack name: `gasprices`
- Region: `ca-central-1`
- **Allow SAM CLI IAM role creation: yes** — it creates the least-privilege
  execution roles from the template rather than you hand-writing IAM policies.
- **Disable rollback: no** — on a failed deploy you want CloudFormation to put
  things back, not leave a half-built stack.
- `LogFunction` may have no authorizer: **yes** (see below).

### Why the Function URL has `AuthType: NONE`

It is not unauthenticated. The Lambda checks a shared-secret header and returns
401 without it. `AWS_IAM` auth would be stronger, but it requires **SigV4
request signing** — an HMAC chain over a canonical request — which is a lot of
firmware on an ESP32 for one endpoint that does one thing. A rotatable secret
over HTTPS is the proportionate choice here.

### Why no VPC — the expensive mistake

Tutorials often put Lambdas in a VPC. **Don't.** This function needs the public
internet (Yahoo, frankfurter, ontario.ca). A Lambda inside a VPC reaches the
internet only through a **NAT Gateway, ~$32/month** — thirty times the rest of
the stack, for zero benefit. Outside a VPC it has internet access for free.

If your budget alarm ever fires, this is the first thing to check.

---

## Part 6 — Verify, then cut over

```bash
# Run the scheduled function on demand
aws lambda invoke --function-name gasprices-build /dev/stdout

# Exercise the ingest endpoint (URL is in the sam deploy outputs)
curl -X POST "$LOG_URL" \
  -H "x-gp-secret: $(aws ssm get-parameter --name /gasprices/ingest_secret \
      --with-decryption --query Parameter.Value --output text)" \
  -d '{"station":"beaver","price":1.709,"source":"phone"}'

# Wrong secret must be refused
curl -X POST "$LOG_URL" -H "x-gp-secret: wrong" -d '{"station":"beaver","price":1.709}'

# Lookup mode: what am I standing at? Writes nothing, returns nearest first.
curl -X POST "$LOG_URL" -H "x-gp-secret: $SECRET" \
  -d '{"lat":43.87578,"lon":-79.41570}'
```

Expect a commit in the repo, `station_prices.csv` updated, `data.json`
republished, and `401` for the bad secret.

**Run both schedulers in parallel for a few days.** Leave the GitHub Action
cron running and compare its output against the Lambda's. Only when they agree
do you remove the `schedule:` block from `update.yml`. Keep
`workflow_dispatch`/`repository_dispatch` as a free fallback — if AWS ever
breaks, one click still publishes.

The device never notices any of this: Pages keeps serving the same URL.

---

## Cost, and the three ways to break it

| Service | Usage here | Cost |
|---|---|---|
| Lambda | ~70 invocations/mo | $0 — 1M requests + 400k GB-s always free |
| EventBridge Scheduler | 60/mo | $0 |
| Function URL | direct, no API Gateway | $0 |
| SSM Parameter Store | 2 Standard params | $0 |
| CloudWatch Logs | tiny, 7-day retention | $0 |

1. **A VPC** → NAT Gateway, ~$32/mo. Never for this function.
2. **Secrets Manager** instead of SSM → $0.40/secret/mo.
3. **Default log retention is "never expire"** → a slow leak. The template pins
   7 days.

### Tearing it down

```bash
sam delete --stack-name gasprices
```

Deletes every resource CloudFormation created. This is the real argument for
infrastructure-as-code over clicking in the console: you can prove what exists
and remove all of it in one command. Delete the SSM parameters separately —
they're deliberately not part of the stack, so a redeploy doesn't destroy your
credentials.
