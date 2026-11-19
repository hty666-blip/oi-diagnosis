import { useEffect, useState } from "react";
import { api, messageOf, type WrittenExamStudyTopic, type WrittenPracticePick } from "../api";
import { ErrorNotice } from "./RecordUI";
import { KnowledgeReader } from "./WrittenKnowledge";

export default function WrittenExamStudy({ examId, onPractice }: {
  examId: number;
  onPractice: (question: WrittenPracticePick) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return <details className="card written-task-topics" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary>按知识点复习</summary>
    {expanded && <StudyTopics examId={examId} onPractice={onPractice} />}
  </details>;
}

function StudyTopics({ examId, onPractice }: {
  examId: number;
  onPractice: (question: WrittenPracticePick) => void;
}) {
  const [topics, setTopics] = useState<WrittenExamStudyTopic[]>();
  const [article, setArticle] = useState<number>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    setError("");
    void api.writtenExamStudy(examId, request.signal)
      .then((result) => { if (!request.signal.aborted) setTopics(result.items); })
      .catch((failure) => { if (!request.signal.aborted) setError(messageOf(failure)); });
    return () => request.abort();
  }, [examId, reload]);

  if (error) return <ErrorNotice message={error} onRetry={() => setReload((value) => value + 1)} />;
  if (!topics) return <p role="status">正在读取复习建议…</p>;
  if (!topics.length) return <p className="muted">这份答卷暂无可复习的知识点。</p>;
  return <>
    <div className="table-scroll"><table aria-label="错题知识点">
      <thead><tr><th scope="col">知识点</th><th scope="col">原卷错题与未答</th><th scope="col">讲义</th><th scope="col">同知识点新题</th></tr></thead>
      <tbody>{topics.map((topic) => {
        const lesson = topic.article;
        const question = topic.next_question;
        return <tr key={`${topic.level}:${topic.point.id}`}>
          <th scope="row">{topic.point.name}{topic.level && <small className="muted">CSP-{topic.level}</small>}</th>
          <td>{topic.numbers.join("、")}</td>
          <td>{lesson ? <button className="ghost" type="button" onClick={() => setArticle(lesson.id)}>{lesson.title}</button>
            : <span className="muted">暂无讲义</span>}</td>
          <td>{question ? <button className="ghost" type="button" onClick={() => onPractice(question)}
            aria-label={`练习「${topic.point.name}」新题`}>练一道新题</button> : <span className="muted">暂无新题</span>}</td>
        </tr>;
      })}</tbody>
    </table></div>
    {article !== undefined && <KnowledgeReader id={article} onClose={() => setArticle(undefined)} />}
  </>;
}
