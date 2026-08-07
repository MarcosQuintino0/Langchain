/**
 * Client HTTP compartilhado do projeto de testes.
 *
 * Fica FORA do diretório do recurso de propósito: `cy.request` só pode morar no
 * client compartilhado (QAAPI-005).
 */
export function apiRequest(opcoes) {
  return cy.request({ failOnStatusCode: false, ...opcoes });
}
