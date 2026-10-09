// Cloudflare Worker that starts the "County gauges and radar" GitHub Action every 15 minutes.
// GitHub's own cron for this repo almost never fires (newly registered schedules have been
// dropped since late August 2026), so this calls the workflow_dispatch API instead.
//
// Setup (Cloudflare dashboard):
//   1. Workers & Pages > Create > Worker, name it alta-hydromet-cron, deploy, then Edit code and paste this file.
//   2. Settings > Variables and Secrets > Add > type Secret, name GH_TOKEN, value: a GitHub
//      fine-grained token limited to Tyler-Y42/Alta.Hydromet with Actions: Read and write.
//   3. Settings > Trigger events > Cron Triggers > add  2,17,32,47 * * * *   (UTC, off the top of the hour)
// Opening the Worker's URL only reports status; it never starts a run.

const REPO = 'Tyler-Y42/Alta.Hydromet';
const WORKFLOW = 'county.yml';

async function dispatch(env) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'alta-hydromet-cron',
    },
    body: JSON.stringify({ ref: 'main', inputs: { trigger: 'cloudflare' } }),
  });
  if (r.status !== 204) console.log(`dispatch failed: HTTP ${r.status} ${await r.text()}`);
  else console.log('dispatched');
  return r.status;
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  async fetch() {
    return new Response('alta-hydromet-cron: starts the GitHub Action on a cron trigger.\n', { headers: { 'content-type': 'text/plain' } });
  },
};
