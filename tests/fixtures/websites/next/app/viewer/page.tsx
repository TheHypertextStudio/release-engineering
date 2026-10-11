import { cookies } from 'next/headers';
export const dynamic = 'force-dynamic';
export default async function Viewer() {
  const viewer = (await cookies()).get('fixture_viewer')?.value ?? 'anonymous';
  return <h1>{'Viewer: ' + viewer}</h1>;
}

