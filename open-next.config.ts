// Cloudflare-Adapter (OpenNext). Kein ISR-Cache nötig: die App-Seiten sind dynamisch, die Website ist statisch.
import { defineCloudflareConfig } from '@opennextjs/cloudflare';

export default defineCloudflareConfig({});
