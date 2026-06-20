# Silvers Syndicate Email Setup

This folder sets up the AWS email foundation from GitHub.

## What this deploys

The GitHub workflow deploys a CloudFormation stack named `silvers-syndicate-email` in `us-east-1`.

It creates:

- SES domain identity for `silverssyndicate.app`
- SES DKIM signing enabled
- SES custom MAIL FROM domain: `mail.silverssyndicate.app`
- SES configuration set: `silvers-syndicate-email`
- SES templates:
  - `silvers-welcome`
  - `silvers-password-reset`
  - `silvers-billing-notice`
  - `silvers-market-alert`

## What this does not fully create

AWS WorkMail inboxes require organization/domain setup and user mailbox registration. The recommended human inboxes are:

- `kurt@silverssyndicate.app`
- `support@silverssyndicate.app`
- `legal@silverssyndicate.app`
- `billing@silverssyndicate.app`
- `no-reply@silverssyndicate.app`

Use WorkMail for human inboxes. Use SES for application-generated emails.

## GitHub Secrets required

The email deploy workflow uses the same AWS secrets as the backend deploy workflow:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

## How to deploy

1. Go to GitHub → Actions.
2. Choose `Deploy Silvers Email`.
3. Click `Run workflow`.
4. Use:
   - `domain_name`: `silverssyndicate.app`
   - `mail_from_subdomain`: `mail`
5. Run the workflow.

## DNS records still required

After the SES identity is created, AWS SES will provide DNS records for domain verification and DKIM.

Add those records wherever DNS is managed for `silverssyndicate.app`:

- SES domain verification TXT/CNAME record if shown
- DKIM CNAME records
- MAIL FROM MX/TXT records for `mail.silverssyndicate.app`

Do not guess these values. Copy them exactly from AWS SES after the stack creates the identity.

## Production sending note

If SES is still in sandbox mode, request production access before customer emails go live.

## Safe launch split

- WorkMail = real inboxes for Kurt/support/legal/billing.
- SES = automated app emails for welcome, password reset, billing notices, and market alerts.
