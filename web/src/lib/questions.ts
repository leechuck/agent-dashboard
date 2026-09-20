export interface AgentQuestion {
  title: string
  question: string
  multiple: boolean
  options: { label: string; description: string }[]
}

export function isQuestionTool(name: string): boolean {
  return ['AskUserQuestion', 'request_user_input', 'request_user_input_async'].includes(name.split('.').at(-1) ?? '')
}

export function agentQuestions(input: Record<string, unknown> | null | undefined, fallback = ''): AgentQuestion[] {
  const raw = Array.isArray(input?.questions) ? input.questions : []
  const questions = raw.flatMap((q): AgentQuestion[] => {
    if (!q || typeof q !== 'object') return []
    const question = typeof q.question === 'string' ? q.question : typeof q.title === 'string' ? q.title : ''
    if (!question) return []
    return [{
      title: typeof q.header === 'string' ? q.header : '',
      question,
      multiple: q.multiSelect === true,
      options: (Array.isArray(q.options) ? q.options : []).flatMap((o: unknown) => {
        if (typeof o === 'string') return [{ label: o, description: '' }]
        if (!o || typeof o !== 'object' || !('label' in o) || typeof o.label !== 'string') return []
        return [{ label: o.label, description: 'description' in o && typeof o.description === 'string' ? o.description : '' }]
      }),
    }]
  })
  return questions.length ? questions : [{ title: '', question: fallback || 'The agent is asking for your input.', multiple: false, options: [] }]
}
