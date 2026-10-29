import { createRoot } from 'react-dom/client';
import { ConversationRecipientChoices } from '../src/ConversationRecipientChoices';
const root = createRoot(document.getElementById('root')!);
(window as any).renderChoices = (props: Record<string, unknown>) => root.render(<ConversationRecipientChoices
  token="qa-token" conversationId="thread-1" turnId="turn-1" taskId="task-1" taskVersion={3} blocked={false}
  onSelect={async selection => { (window as any).selected.push(selection); }} {...props}/>);
(window as any).unmountChoices = () => root.unmount();
