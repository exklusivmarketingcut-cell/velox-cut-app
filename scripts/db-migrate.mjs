// Datenbank einrichten: führt supabase/migrations/*.sql der Reihe nach aus.
// Aufruf: DATABASE_URL=… node scripts/db-migrate.mjs   (wird von scripts/cloudflare-setup.sh benutzt)
import pg from 'pg';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const url = new URL(process.env.DATABASE_URL);
url.searchParams.delete('sslmode');
const db = new pg.Client({ connectionString: url.toString(), ssl: { rejectUnauthorized: false } });
await db.connect();
const dir = join(import.meta.dirname, '..', 'supabase', 'migrations');
const { rows } = await db.query("select count(*)::int as n from information_schema.tables where table_schema = 'public' and table_name = 'profiles'");
if (rows[0].n > 0) {
  console.log('✓ Datenbank ist schon eingerichtet – nichts zu tun.');
} else {
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.sql')).sort()) {
    await db.query(readFileSync(join(dir, f), 'utf8'));
    console.log('✓', f);
  }
}
const t = await db.query("select count(*)::int as n from information_schema.tables where table_schema = 'public'");
const fn = await db.query("select count(*)::int as n from information_schema.routines where routine_schema = 'public'");
console.log(`  ${t.rows[0].n} Tabellen, ${fn.rows[0].n} Funktionen`);
await db.end();
