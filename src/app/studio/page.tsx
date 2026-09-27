import Topbar from '@/components/Topbar';
import { requireProfile } from '@/lib/profile';
import { createClient } from '@/lib/supabase/server';
import { PLANS } from '@/lib/pricing';
import Studio from './Studio';

export const metadata = { title: 'Studio' };

export default async function Page({ searchParams }: PageProps<'/studio'>) {
  const p = await requireProfile({ needCard: true });
  const q = await searchParams;
  const supabase = await createClient();
  const { data: jobs } = await supabase.from('jobs').select('*').order('created_at', { ascending: false }).limit(50);
  return (
    <>
      <Topbar p={p} on="studio" />
      <main className="page">
        <div className="wrap studio">
          {q.willkommen ? <p className="ok" style={{ marginBottom: 16 }}>Karte gespeichert – du bist startklar. Dein erstes Video ist gratis.</p> : null}
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
