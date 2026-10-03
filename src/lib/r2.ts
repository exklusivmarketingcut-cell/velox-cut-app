import 'server-only';
import { AwsClient } from 'aws4fetch';

// Video-Speicher: Cloudflare R2 (S3-kompatibel). Browser und Schnitt-Maschine laden direkt über
// zeitlich begrenzte, signierte Links hoch/runter – die Videos laufen nie durch die App selbst.
// Ordner im Bucket: uploads/<user>/… (Rohvideos) und outputs/<user>/<job>/… (fertige Clips)

let client: AwsClient | undefined;
const r2 = () =>
  (client ??= new AwsClient({
    accessKeyId: process.env.R2_ACCESS_KEY_ID!,
    secretAccessKey: process.env.R2_SECRET_ACCESS_KEY!,
    service: 's3',
    region: 'auto',
  }));

const objectUrl = (key: string) => {
  const endpoint = process.env.R2_ENDPOINT || `https://${process.env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`;
  return new URL(`${endpoint}/${process.env.R2_BUCKET}/${key.split('/').map(encodeURIComponent).join('/')}`);
};

async function presign(method: 'GET' | 'PUT', key: string, seconds: number, params: Record<string, string> = {}) {
  const url = objectUrl(key);
  url.searchParams.set('X-Amz-Expires', String(seconds));
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const signed = await r2().sign(url, { method, aws: { signQuery: true } });
  return signed.url;
}

/** Link, mit dem der Browser genau eine Datei hochladen darf (PUT, 2 Stunden gültig) */
export const uploadUrl = (key: string) => presign('PUT', key, 2 * 3600);

/** Download-Link für einen fertigen Clip (1 Stunde gültig, speichert unter dem angegebenen Namen) */
export const downloadUrl = (key: string, filename: string) =>
  presign('GET', key, 3600, { 'response-content-disposition': `attachment; filename="${filename}"` });

/** Link zum Anzeigen einer Datei im Browser (z. B. Logo-Vorschau, 1 Stunde gültig) */
export const viewUrl = (key: string) => presign('GET', key, 3600);

/** Datei löschen (z. B. altes Logo) */
export async function removeObject(key: string) {
  const signed = await r2().sign(objectUrl(key), { method: 'DELETE' });
  await fetch(signed).catch(() => null);
}
