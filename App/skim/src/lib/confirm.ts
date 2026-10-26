export const DELETE_WORD = 'DELETE';

/** The typed confirmation for deleting an account: exactly the word, ignoring case and surrounding spaces. */
export const confirmsDelete = (typed: string): boolean => typed.trim().toUpperCase() === DELETE_WORD;
