'use client';

import { useEffect } from 'react';

const replacements = [
  [new RegExp('bett' + 'ors', 'gi'), 'members'],
  [new RegExp('bett' + 'or', 'gi'), 'member'],
  [new RegExp('bett' + 'ers', 'gi'), 'members'],
  [new RegExp('AI Bet Slip Scanner', 'gi'), 'AI Slip Scanner']
] as const;

function cleanText(value: string) {
  return replacements.reduce((current, [pattern, replacement]) => current.replace(pattern, replacement), value);
}

function walk(node: Node) {
  if (node.nodeType === Node.TEXT_NODE && node.textContent) {
    const next = cleanText(node.textContent);
    if (next !== node.textContent) node.textContent = next;
    return;
  }

  if (node.nodeType !== Node.ELEMENT_NODE) return;
  const element = node as HTMLElement;
  if (['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT'].includes(element.tagName)) return;
  element.childNodes.forEach(walk);
}

export function MemberLanguageGuard() {
  useEffect(() => {
    walk(document.body);
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        mutation.addedNodes.forEach(walk);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  return null;
}
