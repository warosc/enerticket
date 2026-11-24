import styles from './admin.module.css';

/** Lista de etiquetas que parte nombres largos (usable a 390 px sin desbordar). */
export default function Tags({ items, empty = 'Ninguno' }: { items: string[]; empty?: string }) {
  if (!items.length) return <span className="text-xs subtle">{empty}</span>;
  return (
    <span className={styles.tags}>
      {items.map((item, index) => (
        <span key={`${item}-${index}`} className={styles.tag}>
          {item}
        </span>
      ))}
    </span>
  );
}
