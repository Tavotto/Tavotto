## Failed Tavotto tool calls now tell an AI agent what to do next

When a Tavotto tool call from an AI editor failed, the agent only got an error code and often had to guess what to try. Failed results now carry a short `next` step: the exact tool call to make, a note that the script itself has to change, or a question to put to you with the choices filled in. When "make this figure meet the journal's rules" cannot finish, it now names the rule, the element and the two requirements that clash, and the property involved when there is one. Error codes themselves are unchanged.
