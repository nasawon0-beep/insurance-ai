// One realm lifetime; never persist this generation or derive it from a user ID.
export function createAccountFence() {
  let generation = 0;
  const assert = (issued: number) => {
    if (issued !== generation) throw new Error('계정이 변경되어 이전 요청을 취소했습니다.');
  };
  const response = (value: Response, issued: number): Response => {
    let stream: ReadableStream<Uint8Array> | null | undefined;
    return new Proxy(value, {
      get(target, key) {
        if (key === 'clone') return () => response(target.clone(), issued);
        if (key === 'body') {
          if (stream !== undefined) return stream;
          if (!target.body) return (stream = null);
          const reader = target.body.getReader();
          stream = new ReadableStream({
            async pull(controller) {
              try { assert(issued); const part = await reader.read(); assert(issued);
                if (part.done) controller.close(); else controller.enqueue(part.value);
              } catch (error) { void reader.cancel(); controller.error(error); }
            },
            cancel: reason => reader.cancel(reason),
          });
          return stream;
        }
        const member = Reflect.get(target, key, target);
        if (['json','text','arrayBuffer','blob','formData','bytes'].includes(String(key)) && typeof member === 'function') {
          return async (...args: unknown[]) => { assert(issued); const result = await member.apply(target,args); assert(issued); return result; };
        }
        return typeof member === 'function' ? member.bind(target) : member;
      },
    });
  };
  return {capture: () => generation, invalidate: () => ++generation, assert, response};
}
