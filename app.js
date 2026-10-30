const menuButton = document.querySelector('.menu-toggle');
const mainNav = document.querySelector('.main-nav');
const themeToggle = document.querySelector('[data-theme-toggle]');
const themeLabel = themeToggle?.querySelector('[data-theme-label]');

function applyTheme(theme) {
  const isDark = theme === 'dark';
  document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
  themeToggle?.setAttribute('aria-pressed', String(isDark));
  themeToggle?.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
  if (themeLabel) themeLabel.textContent = isDark ? 'Light' : 'Dark';
}

applyTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
themeToggle?.addEventListener('click', () => {
  const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(nextTheme);
  try {
    localStorage.setItem('codecare-theme', nextTheme);
  } catch {
    // The current choice still applies if browser storage is unavailable.
  }
});

window.addEventListener('storage', (event) => {
  if (event.key === 'codecare-theme') applyTheme(event.newValue === 'dark' ? 'dark' : 'light');
});

menuButton?.addEventListener('click', () => {
  const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
  menuButton.setAttribute('aria-expanded', String(!isOpen));
  menuButton.setAttribute('aria-label', isOpen ? 'Open navigation' : 'Close navigation');
  mainNav?.classList.toggle('open', !isOpen);
});

mainNav?.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => {
    mainNav.classList.remove('open');
    menuButton?.setAttribute('aria-expanded', 'false');
    menuButton?.setAttribute('aria-label', 'Open navigation');
  });
});

const billingButtons = document.querySelectorAll('[data-billing]');
billingButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const billing = button.dataset.billing;
    billingButtons.forEach((option) => option.setAttribute('aria-pressed', String(option === button)));
    document.querySelectorAll('.plan-price strong[data-monthly]').forEach((price) => {
      price.textContent = `$${price.dataset[billing]}`;
    });
    document.querySelectorAll('.billing-detail[data-monthly-copy]').forEach((detail) => {
      detail.textContent = detail.dataset[`${billing}Copy`];
    });
  });
});

const contactForm = document.querySelector('#contact-form');
if (contactForm) {
  const topic = contactForm.querySelector('[name="topic"]');
  const requestedPlan = new URLSearchParams(window.location.search).get('plan');
  const planTopics = { starter: 'plans', pro: 'plans', team: 'team', enterprise: 'team' };
  if (requestedPlan && planTopics[requestedPlan]) topic.value = planTopics[requestedPlan];

  contactForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const fields = [
      { input: contactForm.querySelector('[name="name"]'), error: contactForm.querySelector('#name-error'), message: 'Enter your name.' },
      { input: contactForm.querySelector('[name="email"]'), error: contactForm.querySelector('#email-error'), message: 'Enter a valid email address.' },
      { input: contactForm.querySelector('[name="message"]'), error: contactForm.querySelector('#message-error'), message: 'Write a message of at least 10 characters.' },
    ];
    let valid = true;
    fields.forEach(({ input, error, message }) => {
      const fieldValid = input.checkValidity();
      input.setAttribute('aria-invalid', String(!fieldValid));
      error.textContent = fieldValid ? '' : message;
      valid = valid && fieldValid;
    });
    if (!topic.value) {
      topic.setAttribute('aria-invalid', 'true');
      document.querySelector('#topic-error').textContent = 'Choose a topic.';
      valid = false;
    } else {
      topic.setAttribute('aria-invalid', 'false');
      document.querySelector('#topic-error').textContent = '';
    }

    const status = document.querySelector('#contact-status');
    if (!valid) {
      status.textContent = 'Please check the highlighted fields and try again.';
      status.classList.add('is-error');
      return;
    }

    status.classList.remove('is-error');
    status.textContent = 'Sending…';
    const sendContact = (token) => fetch('/api/v1/contact', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
      body: JSON.stringify({
        name: contactForm.querySelector('[name="name"]').value,
        email: contactForm.querySelector('[name="email"]').value,
        topic: topic.value,
        message: contactForm.querySelector('[name="message"]').value,
      }),
    }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        status.textContent = typeof data.detail === 'string' ? data.detail : 'The message could not be stored.';
        status.classList.add('is-error');
        return;
      }
      status.textContent = data.detail || 'Message stored.';
      status.classList.remove('is-error');
      contactForm.reset();
    }).catch(() => {
      status.textContent = 'The API is not reachable. Start the CodeCare backend and try again.';
      status.classList.add('is-error');
    });
    const existing = document.cookie.split(';').map((part) => part.trim()).find((part) => part.startsWith('codecare_csrf='));
    if (existing) {
      sendContact(decodeURIComponent(existing.slice('codecare_csrf='.length)));
      return;
    }
    fetch('/api/v1/auth/csrf', { credentials: 'include' })
      .then((response) => response.json())
      .then((data) => sendContact(data.csrf_token || ''))
      .catch(() => {
        status.textContent = 'The API is not reachable. Start the CodeCare backend and try again.';
        status.classList.add('is-error');
      });
  });
}

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if (!reducedMotion && 'IntersectionObserver' in window) {
  const revealTargets = document.querySelectorAll(
    '.manifesto-content, .workflow-overview, .feature-heading, .feature-card, .flow-step, .ecosystem-item, .safety-list article, .pricing-inner, .signup-card',
  );

  document.body.classList.add('motion-ready');
  revealTargets.forEach((target) => target.classList.add('reveal'));

  const revealObserver = new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -36px 0px' });

  revealTargets.forEach((target) => revealObserver.observe(target));
}
