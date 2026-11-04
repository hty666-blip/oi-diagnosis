type Name = "trainings" | "submit" | "my" | "profiles" | "coach" | "admin";
const paths: Record<Name, string> = {
  trainings: "M4 4h16v16H4z M4 9h16 M9 9v11",
  submit: "m9 7-5 5 5 5 M15 7l5 5-5 5",
  my: "M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5",
  profiles: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-3a8 8 0 0 1 16 0v3",
  coach: "M4 4h16v16H4z m4 8 3 3 5-6",
  admin: "M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z",
};
export default function NavIcon({ name }: { name: Name }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true"><path d={paths[name]} /></svg>;
}
