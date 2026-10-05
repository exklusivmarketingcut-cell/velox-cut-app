import Link from 'next/link';
import type { Profile } from '@/lib/profile';
import { isAdmin } from '@/lib/admin';

export default function Topbar({ p, on }: { p: Profile; on?: 'studio' | 'konto' | 'admin' }) {
  return (
    <header className="topbar">
      <div className="wrap">
        <Link href="/studio" aria-label="Studio">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/assets/img/logo-full-white.png" alt="Velox Cut" />
        </Link>
        <nav>
          <span className="credit-pill" title="Paket-Credits + nachgekaufte Credits"><b>{p.plan_credits + p.topup_credits}</b> Credits</span>
          <Link href="/studio" className={on === 'studio' ? 'on' : ''}>Studio</Link>
          {isAdmin(p.email) ? <Link href="/admin" className={on === 'admin' ? 'on' : ''}>Admin</Link> : null}
          <Link href="/konto" className={on === 'konto' ? 'on' : ''}>Abo<span className="tb-long"> &amp; Credits</span></Link>
          <form action="/auth/abmelden" method="post"><button className="btn tb-out" style={{ padding: '7px 14px', fontSize: 14 }} aria-label="Abmelden">Abmelden</button></form>
        </nav>
      </div>
    </header>
  );
}
