import Topbar from '@/components/Topbar';
import { requireProfile } from '@/lib/profile';
import Link from 'next/link';
import { createClient, getUser } from '@/lib/supabase/server';
import { PLANS } from '@/lib/pricing';
import Studio from './Studio';

export const metadata = { title: 'Studio' };

export default async function Page({ searchParams }: PageProps<'/studio'>) {
  const p = await requireProfile({ needCard: true });
  const q = await searchParams;
  const supabase = await createClient();
  const { data: jobs } = await supabase.from('jobs').select('*').order('created_at', { ascending: false }).limit(50);
  // Gutschein-Code von der Registrierung noch nicht eingelöst → Hinweis
  const code = String((await getUser())?.user_metadata?.code ?? '');
  const { count: used, error: cErr } = code ? await supabase.from('code_uses').select('code', { count: 'exact', head: true }).eq('code', code) : { count: 1, error: null };
  const openCode = code && !cErr && !used ? code : null;
  return (
    <>
      <Topbar p={p} on="studio" />
      <main className="page">
        <div className="wrap studio">
          {q.willkommen ? <p className="ok" style={{ marginBottom: 16 }}>Karte gespeichert – du bist startklar. Dein erstes Video ist gratis.</p> : null}
          {openCode ? (
            <p className="hint" style={{ marginBottom: 16 }}>
              <span>🎁 Dein Gutschein-Code <b>{openCode}</b> wartet noch.</span>
              <Link className="btn" href={`/konto?code=${encodeURIComponent(openCode)}#code`}>Jetzt einlösen</Link>
            </p>
          ) : null}
          <h1>Neues Video</h1>
          <p className="sub">
            Paket: <b>{PLANS[p.plan].name}</b> · {p.plan_credits} Paket-Credits + {p.topup_credits} nachgekaufte Credits
          </p>
          <Studio credits={p.plan_credits + p.topup_credits} initialJobs={jobs ?? []} />
        </div>
      </main>
    </>
  );
}
