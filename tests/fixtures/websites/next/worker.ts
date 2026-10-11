// @ts-ignore OpenNext generates this JavaScript module during the native build.
import handler from './.open-next/worker.js';
export default {
  fetch(request, env, ctx) {
    if (new URL(request.url).pathname === '/__studio/release') {
      return Response.json({
        sourceSha: env.STUDIO_SOURCE_SHA,
        artifactSha256: env.STUDIO_ARTIFACT_SHA256,
        candidateId: env.STUDIO_CANDIDATE_ID,
      }, { headers: { 'Cache-Control': 'no-store' } });
    }
    return handler.fetch(request, env, ctx);
  }
} satisfies ExportedHandler<Env>;
// @ts-ignore OpenNext generates these exports during the native build.
export { DOQueueHandler, DOShardedTagCache, BucketCachePurge } from './.open-next/worker.js';

