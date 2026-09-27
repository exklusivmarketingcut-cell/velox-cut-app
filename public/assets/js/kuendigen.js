// Kündigungsseite nach § 312k BGB: /api/kuendigung (speichert, beendet Abo, bestätigt per E-Mail) + speicherbare Bestätigung
(() => {
  const f = document.getElementById('kueForm');
  const $ = id => document.getElementById(id);
  const toggle = () => {
    const ao = f.elements.art.value.startsWith('Außer');
    $('kueGrundWrap').hidden = !ao;
    $('kueDatumWrap').hidden = f.elements.zeitpunkt.value !== 'Zu einem bestimmten Datum';
  };
  f.addEventListener('change', toggle);
  toggle();

  const fmt = d => d.toLocaleString('de-DE', { dateStyle: 'long', timeStyle: 'medium' }) + ' Uhr';
  const summary = (e, zeit) => [
    'KÜNDIGUNG – Velox Cut Studio',
    'An: Nunu Alexandru Petru Pasca, Velox Cut, Weiherstraße 23, 73730 Esslingen am Neckar, infoveloxcut@gmail.com',
    '',
    'Abgegeben am: ' + zeit,
    'Art der Kündigung: ' + e.art.value + (e.art.value.startsWith('Außer') ? '\nKündigungsgrund: ' + e.grund.value.trim() : ''),
    'Name: ' + e.name.value.trim(),
    'E-Mail: ' + e.email.value.trim(),
    'Paket: ' + e.paket.value + (e.vertragsnummer.value.trim() ? '\nKunden-/Rechnungsnummer: ' + e.vertragsnummer.value.trim() : ''),
    'Beenden zum: ' + (e.zeitpunkt.value === 'Zu einem bestimmten Datum' ? new Date(e.datum.value).toLocaleDateString('de-DE', { dateStyle: 'long' }) : 'nächstmöglichen Zeitpunkt'),
  ].join('\n');

  const done = text => {
    f.hidden = true;
    $('kueOkText').textContent = text;
    $('kueOk').hidden = false;
    $('kueOk').focus();
    $('kueSave').onclick = () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
      a.download = 'Kuendigung_Velox_Cut.txt';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    };
  };

  f.addEventListener('submit', async ev => {
    ev.preventDefault();
    const e = f.elements, err = $('kueErr');
    const miss = [];
    if (!e.name.value.trim()) miss.push('Name');
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e.email.value.trim())) miss.push('gültige E-Mail-Adresse');
    if (!e.paket.value) miss.push('Paket');
    if (e.art.value.startsWith('Außer') && !e.grund.value.trim()) miss.push('Kündigungsgrund');
    if (e.zeitpunkt.value === 'Zu einem bestimmten Datum' && !e.datum.value) miss.push('Datum');
    if (miss.length) { err.textContent = 'Bitte ergänze: ' + miss.join(', ') + '.'; err.hidden = false; return; }
    err.hidden = true;
    const zeit = fmt(new Date());
    $('kueZeit').value = zeit;
    $('kueOkZeit').textContent = zeit;
    const text = summary(e, zeit);
    const mail = () => { location.href = 'mailto:infoveloxcut@gmail.com?subject=' + encodeURIComponent('Kündigung Velox Cut Studio') + '&body=' + encodeURIComponent(text); };
    if (location.protocol === 'file:') { mail(); return; }
    const btn = f.querySelector('button[type="submit"]');
    btn.disabled = true;
    try {
      const r = await fetch('/api/kuendigung', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(f))) });
      if (!r.ok) throw new Error(r.status);
      done(text);
    } catch {
      btn.disabled = false;
      err.innerHTML = 'Das Absenden hat nicht geklappt. Bitte versuch es noch einmal oder schick deine Kündigung per E-Mail an <a href="mailto:infoveloxcut@gmail.com">infoveloxcut@gmail.com</a>.';
      err.hidden = false;
    }
  });
})();
