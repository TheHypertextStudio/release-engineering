export default {
  fetch(request, env) {
    if (new URL(request.url).pathname === '/__studio/release') {
      return Response.json({
        sourceSha: env.STUDIO_SOURCE_SHA,
        artifactSha256: env.STUDIO_ARTIFACT_SHA256,
        candidateId: env.STUDIO_CANDIDATE_ID,
      }, { headers: { 'Cache-Control': 'no-store' } });
    }
    return env.ASSETS.fetch(request);
  }
} satisfies ExportedHandler<Env>;

