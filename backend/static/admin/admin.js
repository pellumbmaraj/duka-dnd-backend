'use strict';

const loginForm = document.querySelector('[data-login-form]');
if (loginForm) {
  const email = loginForm.querySelector('#email');
  const password = loginForm.querySelector('#password');
  const emailError = loginForm.querySelector('#email-error');
  const passwordError = loginForm.querySelector('#password-error');

  const showError = (input, error, message) => {
    input.classList.add('input-error');
    input.setAttribute('aria-invalid', 'true');
    error.textContent = message;
  };

  const clearError = (input, error) => {
    input.classList.remove('input-error');
    input.removeAttribute('aria-invalid');
    error.textContent = '';
  };

  email.addEventListener('input', () => clearError(email, emailError));
  password.addEventListener('input', () => clearError(password, passwordError));

  loginForm.addEventListener('submit', (event) => {
    clearError(email, emailError);
    clearError(password, passwordError);

    let firstInvalid = null;
    if (!email.value.trim()) {
      showError(email, emailError, 'Please enter your email address.');
      firstInvalid = email;
    } else if (!email.validity.valid) {
      showError(email, emailError, 'Please enter a valid email address.');
      firstInvalid = email;
    }

    if (!password.value) {
      showError(password, passwordError, 'Please enter your password.');
      firstInvalid ||= password;
    }

    if (firstInvalid) {
      event.preventDefault();
      firstInvalid.focus();
    }
  });
}

const passwordToggle = document.querySelector('[data-password-toggle]');
if (passwordToggle) {
  passwordToggle.addEventListener('click', () => {
    const password = document.querySelector('#password');
    const showing = password.type === 'text';
    password.type = showing ? 'password' : 'text';
    passwordToggle.classList.toggle('showing-password', !showing);
    passwordToggle.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
    passwordToggle.setAttribute('aria-pressed', String(!showing));
  });
}

const menuToggle = document.querySelector('[data-menu-toggle]');
if (menuToggle) {
  menuToggle.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    menuToggle.setAttribute('aria-expanded', String(open));
    menuToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  });
}

document.querySelectorAll('[data-copy-value]').forEach((button) => {
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copyValue);
      button.textContent = 'Copied';
    } catch {
      button.textContent = 'Select and copy';
    }
  });
});
