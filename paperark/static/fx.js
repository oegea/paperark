/* PaperArk · capa de animación: revelado al hacer scroll, ondas en botones,
   entrada escalonada y transición entre páginas. Respeta prefers-reduced-motion. */
(function(){
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) return;
  // --- entrada escalonada de lo que ya está en pantalla
  const stagger = (sel, base=60) => { document.querySelectorAll(sel).forEach((el,i)=>{ el.style.animationDelay = (base*i)+'ms'; }); };
  stagger('.card', 70); stagger('.hero > *', 90);
  // --- revelado al hacer scroll (elementos fuera de la vista al cargar)
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { rootMargin: '0px 0px -8% 0px' });
  document.querySelectorAll('.card, .how > div, .opt, .stat > div').forEach(el => {
    const r = el.getBoundingClientRect(); if (r.top > innerHeight) { el.classList.add('reveal'); io.observe(el); }
  });
  // --- onda al pulsar botones y opciones
  document.addEventListener('pointerdown', ev => {
    const b = ev.target.closest('.btn, .opt, .act'); if (!b) return;
    const r = b.getBoundingClientRect(); const s = document.createElement('i'); s.className = 'ripple';
    const d = Math.max(r.width, r.height) * 1.2; s.style.cssText = `width:${d}px;height:${d}px;left:${ev.clientX - r.left - d/2}px;top:${ev.clientY - r.top - d/2}px`;
    b.appendChild(s); setTimeout(() => s.remove(), 650);
  });
  // --- transición suave entre páginas internas
  document.addEventListener('click', ev => {
    const a = ev.target.closest('a[href^="/"]'); if (!a || a.target || ev.metaKey || ev.ctrlKey || a.hasAttribute('download')) return;
    if (a.getAttribute('href').startsWith('/api/')) return;
    ev.preventDefault(); document.body.classList.add('leaving'); setTimeout(() => location.href = a.href, 180);
  });
  addEventListener('pageshow', () => document.body.classList.remove('leaving'));
  // --- el logo saluda
  const logo = document.querySelector('.brand .logo'); if (logo) { logo.classList.add('hello'); }
})();
