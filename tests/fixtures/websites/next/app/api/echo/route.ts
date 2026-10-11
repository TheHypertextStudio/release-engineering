import { cookies } from 'next/headers';
export async function GET(request: Request) {
  return Response.json({
    viewer: (await cookies()).get('fixture_viewer')?.value ?? 'anonymous',
    value: new URL(request.url).searchParams.get('value'),
  }, { headers: { 'Cache-Control': 'private, no-store' } });
}

